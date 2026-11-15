"""Find configured keyboards among the input devices and read their key presses."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import TYPE_CHECKING, Any, Callable

import evdev
from evdev import ecodes

from .state import InputNode

if TYPE_CHECKING:
    from .config import Action, KeyboardConfig
    from .state import AppState, LogEntry

log = logging.getLogger(__name__)

Dispatch = Callable[["Action", "LogEntry"], None]


def key_names(code: int) -> list[str]:
    """All names evdev knows for a key code (some codes have aliases)."""
    names = ecodes.bytype[ecodes.EV_KEY].get(code)
    if names is None:
        return [f"KEY_{code}"]
    return list(names) if isinstance(names, (list, tuple)) else [names]


def has_keys(device: Any) -> bool:
    return ecodes.EV_KEY in device.capabilities()


def find_keyboard(keyboards: list[KeyboardConfig], name: str, uniq: str) -> KeyboardConfig | None:
    for kb in keyboards:
        if kb.match.matches(name, uniq):
            return kb
    return None


class KeyboardManager:
    """Polls for input devices and attaches a reader to each node of a configured keyboard.

    Bluetooth keyboards come and go (sleep, battery, out of range), and their event
    nodes get new numbers each time, so devices are matched by name/MAC on every scan
    rather than by path.
    """

    def __init__(
        self,
        keyboards: list[KeyboardConfig],
        state: AppState,
        dispatch: Dispatch,
        scan_interval: float = 2.0,
        list_devices: Callable[[], list[str]] = evdev.list_devices,
        open_device: Callable[[str], Any] = evdev.InputDevice,
    ) -> None:
        self.keyboards = keyboards
        self.state = state
        self.dispatch = dispatch
        self.scan_interval = scan_interval
        self._list_devices = list_devices
        self._open_device = open_device
        self._known: set[str] = set()  # paths already inspected
        self._readers: dict[str, asyncio.Task] = {}
        self._attached: dict[str, tuple[KeyboardConfig, Any, InputNode]] = {}

    async def run(self) -> None:
        try:
            while True:
                self.scan()
                await asyncio.sleep(self.scan_interval)
        finally:
            await self.close()

    async def close(self) -> None:
        """Stop all readers and release (ungrab) every device."""
        tasks = list(self._readers.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        # A task cancelled before it started never ran its `finally`.
        for path in list(self._attached):
            self._detach(path)

    def scan(self) -> None:
        try:
            present = set(self._list_devices())
        except OSError as err:
            log.warning("cannot list input devices: %s", err)
            return

        for path in self._known - present:
            self._known.discard(path)
            if self.state.unassigned.pop(path, None) is not None:
                self.state.changed()
            if task := self._readers.get(path):
                task.cancel()

        for path in sorted(present - self._known):
            self._known.add(path)
            self._inspect(path)

    def _inspect(self, path: str) -> None:
        try:
            device = self._open_device(path)
        except OSError as err:
            # Usually permissions: needs root or the `input` group.
            log.warning("cannot open %s: %s", path, err)
            self.state.unassigned[path] = InputNode(path, "?", "", "", error=str(err))
            self.state.changed()
            return

        node = InputNode(path, device.name or "", device.uniq or "", device.phys or "")
        kb = find_keyboard(self.keyboards, node.name, node.uniq)
        keys = has_keys(device)
        if kb is None or not keys:
            device.close()
            if kb is None and keys:  # list only devices that could be used as a keyboard
                self.state.unassigned[path] = node
                self.state.changed()
            return

        if kb.grab:
            try:
                device.grab()
                node.grabbed = True
            except OSError as err:
                node.error = f"grab failed: {err}"
                log.warning("cannot grab %s (%s): %s", path, kb.name, err)

        status = self.state.keyboards[kb.name]
        if not status.nodes:
            status.connected_since = time.time()
        status.nodes[path] = node
        log.info("keyboard '%s' attached: %s (%s, %s)", kb.name, path, node.name, node.uniq or "no MAC")
        self._attached[path] = (kb, device, node)
        self._readers[path] = asyncio.create_task(self._read(kb, device, node), name=f"read {path}")
        self.state.changed()

    async def _read(self, kb: KeyboardConfig, device: Any, node: InputNode) -> None:
        try:
            async for event in device.async_read_loop():
                if event.type != ecodes.EV_KEY or event.value != 1:  # key down only
                    continue
                names = key_names(event.code)
                key = next((n for n in names if n in kb.keys), names[0])
                action = kb.keys.get(key)
                entry = self.state.record_key(kb.name, key, action)
                if action is not None:
                    self.dispatch(action, entry)
        except OSError as err:
            log.info("keyboard '%s' node %s went away: %s", kb.name, node.path, err)
        finally:
            self._detach(node.path)

    def _detach(self, path: str) -> None:
        attached = self._attached.pop(path, None)
        self._readers.pop(path, None)
        if attached is None:
            return
        kb, device, node = attached
        with contextlib.suppress(OSError):
            if node.grabbed:
                device.ungrab()
        with contextlib.suppress(OSError):
            device.close()
        # Forget the path so a reappearing node with the same number is re-inspected.
        self._known.discard(path)
        status = self.state.keyboards[kb.name]
        status.nodes.pop(path, None)
        if not status.nodes:
            status.connected_since = None
            log.info("keyboard '%s' disconnected", kb.name)
        self.state.changed()
