"""KeyboardManager hotplug, matching and dispatch with fake evdev devices."""

import asyncio
from types import SimpleNamespace as NS

from evdev import ecodes

from hue_palace.config import parse_config
from hue_palace.keyboards import KeyboardManager
from hue_palace.state import AppState


class FakeDevice:
    def __init__(self, path, name, uniq="", keys=True):
        self.path, self.name, self.uniq, self.phys = path, name, uniq, "bt"
        self._keys = keys
        self.queue: asyncio.Queue = asyncio.Queue()
        self.grabbed = self.closed = False

    def capabilities(self):
        return {ecodes.EV_KEY: [ecodes.KEY_A]} if self._keys else {ecodes.EV_REL: [0]}

    def grab(self):
        self.grabbed = True

    def ungrab(self):
        self.grabbed = False

    def close(self):
        self.closed = True

    def press(self, code):
        self.queue.put_nowait(NS(type=ecodes.EV_KEY, code=code, value=1))
        self.queue.put_nowait(NS(type=ecodes.EV_KEY, code=code, value=0))

    def unplug(self):
        self.queue.put_nowait(OSError(19, "No such device"))

    async def async_read_loop(self):
        while True:
            item = await self.queue.get()
            if isinstance(item, Exception):
                raise item
            yield item


CONFIG = parse_config({
    "bridge": {"host": "h", "app_key": "k"},
    "keyboards": [
        {"name": "remote", "match": {"mac": "AA:BB"},
         "keys": {"KEY_A": {"action": "toggle", "room": "Den"}}},
    ],
})


async def settle():
    for _ in range(5):
        await asyncio.sleep(0)


async def test_hotplug_dispatch_and_unplug():
    devices = {
        "/dev/input/event1": FakeDevice("/dev/input/event1", "Remote", "aa:bb"),
        "/dev/input/event2": FakeDevice("/dev/input/event2", "Remote Mouse", "aa:bb", keys=False),
        "/dev/input/event3": FakeDevice("/dev/input/event3", "Laptop keyboard"),
    }
    state = AppState(CONFIG)
    dispatched = []
    mgr = KeyboardManager(
        CONFIG.keyboards, state, lambda action, entry: dispatched.append(action),
        list_devices=lambda: list(devices), open_device=lambda p: devices[p],
    )

    mgr.scan()
    kb = state.keyboards["remote"]
    assert list(kb.nodes) == ["/dev/input/event1"]  # mouse node has no keys
    assert devices["/dev/input/event1"].grabbed
    assert devices["/dev/input/event2"].closed
    assert list(state.unassigned) == ["/dev/input/event3"]

    devices["/dev/input/event1"].press(ecodes.KEY_A)
    devices["/dev/input/event1"].press(ecodes.KEY_B)  # not mapped: logged, not dispatched
    await settle()
    assert [a.room for a in dispatched] == ["Den"]
    assert kb.presses == 2 and kb.last_key == "KEY_B"
    assert [e.action for e in state.log] == ["toggle room 'Den'", None]

    # Keyboard goes to sleep: node errors out and disappears.
    devices["/dev/input/event1"].unplug()
    del devices["/dev/input/event1"]
    await settle()
    mgr.scan()
    assert not kb.connected
    assert not devices.get("/dev/input/event1")

    # It comes back under a new node number.
    devices["/dev/input/event7"] = FakeDevice("/dev/input/event7", "Remote", "AA:BB")
    mgr.scan()
    assert list(kb.nodes) == ["/dev/input/event7"]

    await mgr.close()
    assert not devices["/dev/input/event7"].grabbed  # released on shutdown
    assert devices["/dev/input/event7"].closed
    assert not kb.connected
