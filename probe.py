#!/usr/bin/env python3
"""M0 probe: prove we can open MI_00 and read the device's identity/version.

This is the feasibility gate. It is deliberately read-only: enumerate, open
interface 0, handshake, read the loaded preset's metadata, decode the firmware
version. It never writes and never resets the device.

Run it in the project venv:

    .venv\\Scripts\\python probe.py        # Windows
    .venv/bin/python probe.py             # Linux/macOS

If it reports "no HX Stomp found", bind WinUSB to MI_00 first (see README).
If it reports it cannot claim interface 0, HX Edit is probably running, or the
device is wedged and needs its 9V adapter pulled.
"""

from __future__ import annotations

import sys

from hx_mcp import session as session_mod
from hx_mcp.protocol import opcodes as op
from hx_mcp.session import HxError
from hx_mcp.tools import device
from hx_mcp.transport import usb


def main() -> int:
    print("HX Stomp M0 probe - read-only")
    print("=" * 40)

    print("[1/4] Enumerating USB (VID 0x%04X)..." % op.VENDOR_ID)
    try:
        devices = usb.list_devices()
    except Exception as exc:
        print(f"  ! enumeration failed: {exc}")
        return 2

    try:
        if not devices:
            print("  ! no supported HX device found.")
            print("    - Is the USB cable connected?")
            print("    - Is WinUSB bound to MI_00? (See README, the Zadig step.)")
            print("    - On Windows, MI_00 with no driver shows as Problem Code 28.")
            return 2

        for d in devices:
            print(f"  - {d.name}  VID=0x{d.vendor_id:04X} PID=0x{d.product_id:04X} "
                  f"serial={d.serial} usb={d.usb_version}")

        found = devices[0]
        print("[2/4] Opening interface %d (EP 0x%02X OUT / 0x%02X IN)..."
              % (op.INTERFACE, op.EP_OUT, op.EP_IN))
        try:
            with device.open_session(found) as session:
                print("[3/4] Handshake (3 channels, draining notifications)...")
                setlist, index, name = session.preset_info()
                print(f"  current preset: setlist {setlist}, index {index} ({name})")

                blob = session.read_preset_blob()
                _, _, tone = session_mod.parse_preset(blob)
                firmware = session_mod.firmware_version(tone)

                print("[4/4] Version")
                print(f"  USB device release : {found.usb_version}")
                print(f"  Firmware (from doc): {firmware or 'unknown'}")
                print()
                print("M0 PASSED - interface 0 is ours and the device answers.")
                return 0
        except (HxError, usb.TransportError) as exc:
            print(f"  ! {exc}")
            _recovery_hint()
            return 1
        except Exception as exc:  # noqa: BLE001 - surface anything for diagnosis
            print(f"  ! unexpected error: {type(exc).__name__}: {exc}")
            _recovery_hint()
            return 1
    finally:
        for d in devices:
            usb.dispose(d)


def _recovery_hint() -> None:
    print()
    print("Recovery guidance:")
    print("  * If HX Edit is running, quit it - it holds interface 0 exclusively.")
    print("  * If the device wedged (took a few operations then went silent),")
    print("    PULL THE 9V ADAPTER for a few seconds. A USB replug is not enough;")
    print("    the unit is externally powered and keeps its session across a replug.")
    print("  * Never USB-reset the device; it leaves the bus and does not return.")


if __name__ == "__main__":
    sys.exit(main())
