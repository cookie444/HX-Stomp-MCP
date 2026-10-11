"""Transport layer: enumerate and talk to the HX Stomp's vendor interface.

The live transport is the pair of bulk endpoints on interface 0, wrapped in a
small :class:`UsbWire` that mirrors the ``Wire`` trait in the reference
implementation: :meth:`UsbWire.send` writes one frame, :meth:`UsbWire.recv`
reads one transfer with a timeout.

This module abstracts the wire so a :class:`~hx_mcp.session.Session` can run
against a recorded transcript instead of hardware (offline tests), and so the
USB backend stays swappable for Linux/macOS later. It is written for **pyusb**
on a libusb/WinUSB backend.

SAFETY: this module never calls ``usb reset``. Resetting an HX Stomp takes it
off the bus and it does not re-enumerate; recovering needs a physical replug.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

import usb.core
import usb.util

from hx_mcp.protocol import opcodes as op


def _backend():
    """A libusb backend, preferring the one bundled by ``libusb-package``.

    On Windows pyusb has no libusb of its own and reports "No backend
    available" unless ``libusb-1.0.dll`` is on the path. The ``libusb-package``
    wheel ships it; fall back to pyusb's own search when it is absent.
    """
    try:
        import libusb_package
        import usb.backend.libusb1

        return usb.backend.libusb1.get_backend(find_library=libusb_package.find_library)
    except Exception:
        return None


class TransportError(Exception):
    """A USB transfer failed."""


class TransportTimeout(TransportError):
    """A transfer timed out. The message matches the reference wire strings."""


class Wire(Protocol):
    """The raw byte transport beneath the frame protocol."""

    def send(self, data: bytes) -> None: ...
    def recv(self, timeout: float) -> bytes:
        """Return the next transfer's bytes. Empty bytes == zero-length transfer.

        Raises :class:`TransportTimeout` when nothing arrives in time.
        """
        ...

    def close(self) -> None: ...


@dataclass
class FoundDevice:
    vendor_id: int
    product_id: int
    product: Optional[str]
    serial: Optional[str]
    #: USB device release number (bcdDevice), e.g. 0x0200 -> "2.00".
    bcd_device: int
    #: The pyusb device handle (opaque; kept for opening).
    device: object

    @property
    def name(self) -> str:
        return op.PRODUCT_IDS.get(self.product_id, f"0x{self.product_id:04x}")

    @property
    def usb_version(self) -> str:
        return f"{self.bcd_device >> 8}.{self.bcd_device & 0xFF:02d}"


def list_devices(vendor_id: int = op.VENDOR_ID) -> list[FoundDevice]:
    """Every supported HX device currently attached (empty == none found).

    NOTE: each entry holds an *open* libusb handle. Callers MUST call
    :func:`dispose` on entries they do not keep, or the device stays open and
    other processes (and the same long-lived process) get "Access denied" on
    the next claim.
    """
    found: list[FoundDevice] = []
    try:
        devices = usb.core.find(find_all=True, idVendor=vendor_id, backend=_backend())
    except Exception as exc:  # backend missing, permissions, etc.
        raise TransportError(f"could not enumerate USB devices: {exc}") from exc
    for dev in devices or []:
        pid = getattr(dev, "idProduct", None)
        if pid not in op.PRODUCT_IDS:
            dispose_device(dev)
            continue
        try:
            product = dev.product
        except Exception:
            product = None
        try:
            serial = dev.serial_number
        except Exception:
            serial = None
        found.append(
            FoundDevice(
                vendor_id=getattr(dev, "idVendor", vendor_id),
                product_id=pid,
                product=product,
                serial=serial,
                bcd_device=getattr(dev, "bcdDevice", 0),
                device=dev,
            )
        )
    return found


def dispose_device(dev) -> None:
    """Release a pyusb device handle. Safe to call more than once."""
    try:
        usb.util.dispose_resources(dev)
    except Exception:
        pass


def dispose(found: "FoundDevice") -> None:
    """Release the handle held by a :class:`FoundDevice`."""
    dispose_device(found.device)


class UsbWire:
    """The live USB transport: one bulk OUT endpoint, one bulk IN endpoint."""

    READ_SIZE = op.EP_MAX_PACKET

    def __init__(self, device) -> None:
        self._dev = device
        self._claimed = False

    def open(self) -> "UsbWire":
        dev = self._dev
        # Ensure the device is configured. This is not a reset.
        try:
            cfg = dev.get_active_configuration()
            if cfg is None:
                dev.set_configuration()
        except usb.core.USBError:
            # Already configured, or the backend reports it oddly; continue and
            # let the claim below surface a real error if there is one.
            pass

        # Claiming immediately after another client releases can briefly return
        # EBUSY; absorb that short hand-off window, then report a real clash.
        # pyusb raises NotImplementedError (not USBError) when the interface has
        # no libusb-compatible driver, which on Windows means WinUSB is not bound.
        last: Optional[Exception] = None
        for attempt in range(7):
            try:
                usb.util.claim_interface(dev, op.INTERFACE)
                self._claimed = True
                break
            except Exception as exc:  # noqa: BLE001 - classify below
                last = exc
                if _is_no_driver(exc):
                    break
                if "busy" not in str(exc).lower():
                    break
                if attempt < 6:
                    import time

                    time.sleep(0.05)
        if not self._claimed:
            if _is_no_driver(last):
                raise TransportError(
                    f"could not claim interface {op.INTERFACE}: no usable driver "
                    "is bound to MI_00.\n"
                    "On Windows this is the WinUSB step - bind it to the "
                    "interface with VID 0E41 / PID 4246 / MI_00 using Zadig "
                    "(see README). Leave MI_01 (audio) and MI_05 (HID) alone."
                )
            raise TransportError(
                f"could not claim interface {op.INTERFACE} ({last}).\n"
                "If HX Edit is running, quit it - it holds the interface "
                "exclusively.\nIf nothing is running, the device is wedged: "
                "pull its 9V adapter for a few seconds. A USB replug is not "
                "enough, because the unit is externally powered and keeps its "
                "session across re-enumeration."
            )

        # Halt/clear both endpoints for a known-clean starting point. This is
        # clear_halt, NOT a device reset.
        for ep in (op.EP_OUT, op.EP_IN):
            try:
                dev.clear_halt(ep)
            except Exception:
                pass
        return self

    def send(self, data: bytes) -> None:
        try:
            self._dev.write(op.EP_OUT, data, timeout=2000)
        except usb.core.USBTimeoutError as exc:
            raise TransportTimeout("write timed out") from exc
        except usb.core.USBError as exc:
            if _is_timeout(exc):
                raise TransportTimeout("write timed out") from exc
            raise TransportError(str(exc)) from exc

    def recv(self, timeout: float) -> bytes:
        ms = max(1, int(timeout * 1000))
        try:
            data = self._dev.read(op.EP_IN, self.READ_SIZE, timeout=ms)
        except usb.core.USBTimeoutError as exc:
            raise TransportTimeout("read timed out") from exc
        except usb.core.USBError as exc:
            if _is_timeout(exc):
                raise TransportTimeout("read timed out") from exc
            raise TransportError(str(exc)) from exc
        return bytes(data) if data is not None else b""

    def close(self) -> None:
        if self._claimed:
            try:
                usb.util.release_interface(self._dev, op.INTERFACE)
            except Exception:
                pass
            self._claimed = False
        try:
            usb.util.dispose_resources(self._dev)
        except Exception:
            pass

    def __enter__(self) -> "UsbWire":
        return self.open()

    def __exit__(self, *exc) -> None:
        self.close()


def _is_timeout(exc: Exception) -> bool:
    errno = getattr(exc, "errno", None)
    if errno == 110:  # ETIMEDOUT (Linux/libusb)
        return True
    return "timed out" in str(exc).lower() or "timeout" in str(exc).lower()


def _is_no_driver(exc: Optional[Exception]) -> bool:
    """True when the interface has no libusb-compatible driver bound."""
    if isinstance(exc, NotImplementedError):
        return True
    text = str(exc).lower()
    return "not supported" in text or "unimplemented" in text or "not implemented" in text
