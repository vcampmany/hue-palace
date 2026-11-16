"""Listen for BTHome BLE button advertisements (e.g. Shelly BLU RC Button 4)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import TYPE_CHECKING, Any, Callable

from .bthome import SERVICE_UUID, BTHomeError, Packet, parse
from .state import BleDevice

if TYPE_CHECKING:
    from .config import Action, BleButtonConfig
    from .state import AppState, LogEntry

log = logging.getLogger(__name__)

Dispatch = Callable[["Action", "LogEntry"], None]


def make_scanner(callback: Callable[[Any, Any], None], adapter: str | None = None) -> Any:
    from bleak import BleakScanner

    bluez: dict[str, Any] = {
        # Bleak asks BlueZ to drop repeated identical adverts by default; we need them
        # (two identical presses in a row) and de-duplicate ourselves.
        "filters": {"DuplicateData": True},
    }
    if adapter:
        bluez["adapter"] = adapter
    return BleakScanner(detection_callback=callback, bluez=bluez)


class Deduplicator:
    """Decides whether a packet is a new event or a repeat of one already handled.

    BTHome devices send each event several times. In order of preference:
      * encrypted packets: the counter must increase (this also rejects replays);
      * packets with a packet id (0x00): a new id means a new event;
      * otherwise: an identical payload within REPEAT_WINDOW seconds is a repeat.
    """

    REPEAT_WINDOW = 2.0

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._counters: dict[str, int] = {}
        self._last: dict[str, tuple[object, float]] = {}

    def is_new(self, mac: str, packet: Packet) -> bool:
        if packet.counter is not None:
            last = self._counters.get(mac)
            if last is not None and packet.counter <= last:
                return False
            self._counters[mac] = packet.counter
            return True
        now = self._clock()
        ident: object = ("id", packet.packet_id) if packet.packet_id is not None else packet.payload
        prev = self._last.get(mac)
        self._last[mac] = (ident, now)
        if prev is None or prev[0] != ident:
            return True
        return packet.packet_id is None and now - prev[1] >= self.REPEAT_WINDOW


class BleManager:
    """Runs a BLE scan and turns BTHome button events into actions."""

    WATCHDOG_SECONDS = 180  # restart the scan if *nothing at all* was heard for this long

    def __init__(
        self,
        buttons: list[BleButtonConfig],
        state: AppState,
        dispatch: Dispatch,
        adapter: str | None = None,
        scanner_factory: Callable[..., Any] = make_scanner,
    ) -> None:
        self.by_mac = {b.mac: b for b in buttons}
        self.state = state
        self.dispatch = dispatch
        self.adapter = adapter
        self._scanner_factory = scanner_factory
        self.dedup = Deduplicator()
        self._last_any = time.monotonic()

    def _set_scanner(self, running: bool, error: str | None = None) -> None:
        s = self.state.ble_scanner
        if s["running"] != running or s["error"] != error:
            s["running"], s["error"] = running, error
            self.state.changed()

    async def run(self) -> None:
        delay = 2
        while True:
            try:
                scanner = self._scanner_factory(self.on_advertisement, self.adapter)
                await scanner.start()
            except Exception as err:  # noqa: BLE001 - no adapter, bluetoothd down, D-Bus denied...
                msg = f"{type(err).__name__}: {err}" if str(err) else type(err).__name__
                log.warning("BLE scan failed to start: %s (retry in %ss)", msg, delay)
                self._set_scanner(False, msg)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60)
                continue
            delay = 2
            log.info("BLE scan running, listening for %d button(s)", len(self.by_mac))
            self._last_any = time.monotonic()
            self._set_scanner(True)
            try:
                while time.monotonic() - self._last_any < self.WATCHDOG_SECONDS:
                    await asyncio.sleep(10)
                log.warning("no BLE advertisements for %ss, restarting the scan", self.WATCHDOG_SECONDS)
                self._set_scanner(False, "no advertisements, restarting scan")
            finally:
                with contextlib.suppress(Exception):
                    await scanner.stop()

    def on_advertisement(self, device: Any, adv: Any) -> None:
        """bleak detection callback; called for every advertisement heard."""
        self._last_any = time.monotonic()
        data = adv.service_data.get(SERVICE_UUID)
        if data is None:
            return
        mac = device.address.lower()
        cfg = self.by_mac.get(mac)
        if cfg is None:
            self._record_unassigned(mac, device, adv, data)
            return

        st = self.state.ble_buttons[cfg.name]
        st.last_heard, st.rssi = time.time(), adv.rssi
        try:
            packet = parse(data, mac, cfg.bindkey_bytes)
        except BTHomeError as err:
            if st.error != str(err):
                log.warning("BLE button '%s': %s", cfg.name, err)
            st.error = str(err)
            self.state.changed()
            return
        st.encrypted = packet.encrypted
        if packet.encrypted and cfg.bindkey is None:
            st.error = "packets are encrypted: add its bindkey to the config"
            self.state.changed()
            return
        st.error = None
        if not self.dedup.is_new(mac, packet):
            return
        if packet.battery is not None:
            st.battery = packet.battery
        mappings = cfg.mappings()
        for number, event in packet.events:
            key = f"button{number}:{event}"
            action = mappings.get(key)
            log.info("BLE button '%s': %s%s", cfg.name, key, "" if action else " (not mapped)")
            entry = self.state.record_key(cfg.name, key, action)
            if action is not None:
                self.dispatch(action, entry)
        self.state.changed()

    def _record_unassigned(self, mac: str, device: Any, adv: Any, data: bytes) -> None:
        try:
            packet = parse(data, mac)
        except BTHomeError:
            return
        known = self.state.ble_unassigned.get(mac)
        events = ", ".join(f"button{n}:{e}" for n, e in packet.events) or None
        self.state.ble_unassigned[mac] = BleDevice(
            mac=mac,
            name=getattr(adv, "local_name", None) or getattr(device, "name", None) or "",
            rssi=adv.rssi,
            last_heard=time.time(),
            encrypted=packet.encrypted,
            last_event=events or (known.last_event if known else None),
        )
        if known is None or events:
            self.state.changed()
