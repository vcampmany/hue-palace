"""Name resolution and action execution against a fake bridge."""

from types import SimpleNamespace as NS

import pytest

from hue_palace.config import Action
from hue_palace.hue import ResolveError, execute, resolve


class FakeController:
    def __init__(self, items):
        self._items = {i.id: i for i in items}
        self.calls = []

    def __iter__(self):
        return iter(self._items.values())

    def __getitem__(self, id):
        return self._items[id]

    def get(self, id, default=None):
        return self._items.get(id, default)

    async def set_state(self, id, **kw):
        self.calls.append(("set_state", id, kw))

    async def set_dimming_delta(self, id, delta):
        self.calls.append(("dim", id, delta))


class FakeScenes(FakeController):
    def __init__(self, items, groups):
        super().__init__(items)
        self._groups = groups

    async def recall(self, id):
        self.calls.append(("recall", id))

    def get_group(self, id):
        rid = self._items[id].group.rid
        return next((g for g in self._groups if g.id == rid), None)


def meta(name):
    return NS(name=name)


@pytest.fixture
def bridge():
    living = NS(id="room-1", metadata=meta("Living room"), grouped_light="gl-1")
    den = NS(id="room-2", metadata=meta("Den"), grouped_light="gl-2")
    empty = NS(id="room-3", metadata=meta("Empty"), grouped_light=None)
    upstairs = NS(id="zone-1", metadata=meta("Upstairs"), grouped_light="gl-3")
    groups = [living, den, empty, upstairs]
    return NS(
        lights=FakeController([
            NS(id="l-1", metadata=meta("Wheel"), is_on=True),
            NS(id="l-2", metadata=meta("Desk lamp"), is_on=False),
        ]),
        devices=FakeController([NS(id="d-1", metadata=meta("Strip"), lights={"l-9"})]),
        scenes=FakeScenes(
            [
                NS(id="s-1", metadata=meta("Relax"), group=NS(rid="room-1")),
                NS(id="s-2", metadata=meta("Relax"), group=NS(rid="room-2")),
                NS(id="s-3", metadata=meta("Cycle"), group=NS(rid="room-2")),
            ],
            groups,
        ),
        groups=NS(
            room=FakeController([living, den, empty]),
            zone=FakeController([upstairs]),
            grouped_light=FakeController([
                NS(id="gl-1", on=NS(on=False)),
                NS(id="gl-2", on=NS(on=True)),
                NS(id="gl-3", on=None),
            ]),
        ),
    )


def act(**kw):
    return Action(**kw)


def test_resolve_scene_unique_and_case_insensitive(bridge):
    assert resolve(bridge, act(action="scene", scene="cycle")).ids == ("s-3",)


def test_resolve_scene_ambiguous_needs_room(bridge):
    with pytest.raises(ResolveError, match="several rooms"):
        resolve(bridge, act(action="scene", scene="Relax"))
    assert resolve(bridge, act(action="scene", scene="Relax", room="den")).ids == ("s-2",)


def test_resolve_scene_wrong_room(bridge):
    with pytest.raises(ResolveError, match="no scene 'Cycle' in room"):
        resolve(bridge, act(action="scene", scene="Cycle", room="Living room"))


def test_resolve_unknown_room_lists_known(bridge):
    with pytest.raises(ResolveError, match="known: Den, Empty, Living room"):
        resolve(bridge, act(action="on", room="Kitchen"))


def test_resolve_room_without_lights(bridge):
    with pytest.raises(ResolveError, match="has no lights"):
        resolve(bridge, act(action="on", room="Empty"))


def test_resolve_light_by_device_name(bridge):
    assert resolve(bridge, act(action="on", light="strip")).ids == ("l-9",)


async def test_toggle_room_uses_grouped_light_state(bridge):
    assert await execute(bridge, act(action="toggle", room="Living room")) == "turned on"
    assert await execute(bridge, act(action="toggle", room="Den")) == "turned off"
    assert await execute(bridge, act(action="toggle", zone="Upstairs")) == "turned on"
    assert bridge.groups.grouped_light.calls == [
        ("set_state", "gl-1", {"on": True}),
        ("set_state", "gl-2", {"on": False}),
        ("set_state", "gl-3", {"on": True}),
    ]


async def test_toggle_light(bridge):
    await execute(bridge, act(action="toggle", light="Wheel"))
    await execute(bridge, act(action="toggle", light="Desk lamp"))
    assert bridge.lights.calls == [
        ("set_state", "l-1", {"on": False}),
        ("set_state", "l-2", {"on": True}),
    ]


async def test_brightness_and_scene(bridge):
    await execute(bridge, act(action="brightness_down", room="Den", step=25))
    await execute(bridge, act(action="scene", scene="Cycle"))
    assert bridge.groups.grouped_light.calls == [("dim", "gl-2", -25)]
    assert bridge.scenes.calls == [("recall", "s-3")]
