"""A long-lived USB session for the MCP server.

Opening and closing the device on every tool call is what wedged the pedal: an
edit left in the buffer with nobody connected lets the device's outgoing
notification queue back up until it stops draining the IN endpoint. HX Edit
never has this problem because it **keeps the editor session open**.

This module does the same: one persistent :class:`~hx_mcp.session.Session` plus
a background thread that drains unsolicited notifications and keeps the channels
alive, so an unsaved edit can safely sit in the buffer while the user scrolls the
panel (which reloads the preset and discards it, exactly like HX Edit).

All wire access is serialised by a re-entrant lock, so the keepalive thread and
tool calls never touch the endpoints at the same time.
"""

from __future__ import annotations

import atexit
import threading
import time
from contextlib import contextmanager
from typing import Iterator, Optional

from hx_mcp.protocol import opcodes as op
from hx_mcp.session import HxError, HxTimeout, Session
from hx_mcp.transport import usb


class SessionManager:
    """Owns one persistent session and a background drain/keepalive thread."""

    #: The device drops quiet sessions; the reference keepalives about once a
    #: second per channel.
    KEEPALIVE_INTERVAL = 1.0

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._session: Optional[Session] = None
        self._wire: Optional[usb.UsbWire] = None
        self._found: Optional[usb.FoundDevice] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._last_error: Optional[Exception] = None

    # ---------------------------------------------------------- lifecycle ---

    def _connect_locked(self) -> None:
        found = usb.list_devices()
        if not found:
            raise HxError(
                "no HX Stomp (VID 0E41) found. Check the USB cable and that "
                "WinUSB is bound to MI_00 (see README: Zadig step)."
            )
        device = found[0]
        for extra in found[1:]:
            usb.dispose(extra)

        last: Optional[Exception] = None
        wire: Optional[usb.UsbWire] = None
        for attempt in range(2):
            try:
                wire = usb.UsbWire(device.device).open()
                session = Session.bring_up(wire)
            except (HxTimeout, HxError) as exc:
                last = exc
                if wire is not None:
                    wire.close()
                else:
                    usb.dispose(device)
                if attempt == 0:
                    # The device ignores a fresh session's opening handshake on
                    # almost every other attempt; re-enumerate and retry once.
                    usb.dispose(device)
                    fresh = usb.list_devices()
                    if not fresh:
                        raise last
                    device = fresh[0]
                    for extra in fresh[1:]:
                        usb.dispose(extra)
                    continue
                raise
            self._session = session
            self._wire = wire
            self._found = device
            self._last_error = None
            self._start_keepalive()
            return

    def _close_locked(self) -> None:
        if self._session is not None:
            try:
                self._session.close()
            except Exception:
                pass
        if self._wire is not None:
            try:
                self._wire.close()
            except Exception:
                pass
        self._session = None
        self._wire = None
        self._found = None

    def _start_keepalive(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._keepalive_loop, name="hx-keepalive", daemon=True
        )
        self._thread.start()

    def _keepalive_loop(self) -> None:
        while not self._stop.wait(self.KEEPALIVE_INTERVAL):
            with self._lock:
                session = self._session
                if session is None:
                    continue
                try:
                    # Drain notifications, then keepalive (whose frames carry
                    # the current acknowledgements) - exactly what a connected
                    # editor does, and what keeps the device from backing up.
                    session.poll_notifications()
                    session.keepalive()
                except Exception as exc:  # device dropped, wire died, ...
                    self._last_error = exc
                    self._close_locked()
                    return

    # ------------------------------------------------------------- public ---

    def device_info(self) -> Optional[usb.FoundDevice]:
        """Identity of the held device, connecting if necessary."""
        with self._lock:
            if self._found is None:
                try:
                    self._connect_locked()
                except Exception as exc:
                    self._last_error = exc
                    return None
            return self._found

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Yield the live session for the duration of a tool call."""
        with self._lock:
            if self._session is None or self._session.poisoned or self._last_error is not None:
                self._close_locked()
                self._connect_locked()
            assert self._session is not None
            yield self._session

    def discard(self) -> bool:
        """Reload the current preset, throwing away any pending edit buffer."""
        with self._lock:
            if self._session is None:
                return False
            try:
                setlist, index, _ = self._session.preset_info()
                return self._session.select_preset(setlist, index)
            except Exception:
                return False

    def close(self) -> None:
        """Leave the device clean (discard any pending edit) and release it."""
        self._stop.set()
        with self._lock:
            if self._session is not None:
                try:
                    setlist, index, _ = self._session.preset_info()
                    self._session.select_preset(setlist, index)
                except Exception:
                    pass
            self._close_locked()


#: Process-wide singleton, closed on interpreter exit.
manager = SessionManager()
atexit.register(manager.close)
