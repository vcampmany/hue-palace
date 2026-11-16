"""Hue bridge connection, name resolution and action execution."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from aiohue import HueBridgeV2
from aiohue.errors import Unauthorized
from aiohue.v2.controllers.events import EventType

from .config import Action, ActionType

if TYPE_CHECKING:
    from .state import AppState, LogEntry

log = logging.getLogger(__name__)


class ResolveError(Exception):
    """A configured name doesn't match anything (or matches too much) on the bridge."""


@dataclass(frozen=True)
class Target:
    kind: str  # "light" | "grouped_light" | "scene"
    ids: tuple[str, ...]


def _name(item: Any) -> str:
    metadata = getattr(item, "metadata", None)
    return (getattr(metadata, "name", None) or "") if metadata else ""


def _same(a: str, b: str) -> bool:
    return a.strip().casefold() == b.strip().casefold()


def _find_group(bridge: Any, kind: str, name: str) -> Any:
    controller = bridge.groups.room if kind == "room" else bridge.groups.zone
    found = [g for g in controller if _same(_name(g), name)]
    if not found:
        known = ", ".join(sorted(_name(g) for g in controller)) or "none"
        raise ResolveError(f"no {kind} named '{name}' (known: {known})")
    return found[0]


def resolve(bridge: Any, action: Action) -> Target:
    """Turn the names in an action into bridge resource ids."""
    if action.action is ActionType.SCENE:
        candidates = [s for s in bridge.scenes if _same(_name(s), action.scene)]
        if action.target_kind:
            group = _find_group(bridge, action.target_kind, action.target_name)
            candidates = [s for s in candidates if s.group.rid == group.id]
            if not candidates:
                raise ResolveError(
                    f"no scene '{action.scene}' in {action.target_kind} '{action.target_name}'"
                )
        elif not candidates:
            raise ResolveError(f"no scene named '{action.scene}'")
        elif len(candidates) > 1:
            groups = sorted(
                _name(g) for s in candidates if (g := bridge.scenes.get_group(s.id)) is not None
            )
            raise ResolveError(
                f"scene '{action.scene}' exists in several rooms/zones ({', '.join(groups)}); "
                "add 'room:' or 'zone:' to pick one"
            )
        return Target("scene", (candidates[0].id,))

    if action.target_kind == "light":
        ids = [light.id for light in bridge.lights if _same(_name(light), action.light)]
        if not ids:
            # Fall back to the device name (a device can own one or more light services).
            for device in bridge.devices:
                if _same(_name(device), action.light):
                    ids.extend(sorted(device.lights))
        if not ids:
            raise ResolveError(f"no light named '{action.light}'")
        return Target("light", tuple(dict.fromkeys(ids)))

    group = _find_group(bridge, action.target_kind, action.target_name)
    if not group.grouped_light:
        raise ResolveError(f"{action.target_kind} '{action.target_name}' has no lights")
    return Target("grouped_light", (group.grouped_light,))


def _is_on(bridge: Any, target: Target) -> bool:
    if target.kind == "light":
        return any(bridge.lights[i].is_on for i in target.ids)
    grouped = bridge.groups.grouped_light[target.ids[0]]
    return bool(grouped.on and grouped.on.on)


async def execute(bridge: Any, action: Action) -> str:
    """Run one action against the bridge. Returns a short result description."""
    target = resolve(bridge, action)
    if target.kind == "scene":
        await bridge.scenes.recall(target.ids[0])
        return "recalled"

    controller = bridge.lights if target.kind == "light" else bridge.groups.grouped_light
    kind = action.action
    if kind is ActionType.TOGGLE:
        kind = ActionType.OFF if _is_on(bridge, target) else ActionType.ON

    for target_id in target.ids:
        if kind is ActionType.ON:
            await controller.set_state(target_id, on=True)
        elif kind is ActionType.OFF:
            await controller.set_state(target_id, on=False)
        elif kind is ActionType.BRIGHTNESS_UP:
            await controller.set_dimming_delta(target_id, action.step)
        elif kind is ActionType.BRIGHTNESS_DOWN:
            await controller.set_dimming_delta(target_id, -action.step)
    return {ActionType.ON: "turned on", ActionType.OFF: "turned off"}.get(kind, "ok")


class HueController:
    """Keeps one connection to the bridge and runs actions from a queue, one at a time."""

    QUEUE_SIZE = 10

    def __init__(self, host: str, app_key: str, state: AppState) -> None:
        self.host = host
        self.app_key = app_key
        self.state = state
        self.bridge: HueBridgeV2 | None = None
        self._queue: asyncio.Queue[tuple[Action, LogEntry]] = asyncio.Queue(self.QUEUE_SIZE)
        state.hue_snapshot = self.snapshot
        state.check_action = self.check

    # -- connection ------------------------------------------------------------

    @property
    def ready(self) -> bool:
        return self.bridge is not None and self.state.bridge.connected

    def _set_connected(self, connected: bool, error: str | None = None) -> None:
        b = self.state.bridge
        if b.connected != connected or b.error != error:
            b.connected, b.error = connected, error
            b.since = time.time()
            self.state.changed()

    def _on_event(self, event_type: EventType, _item: Any) -> None:
        if event_type in (EventType.CONNECTED, EventType.RECONNECTED):
            self._set_connected(True)
        elif event_type is EventType.DISCONNECTED:
            self._set_connected(False, "event stream lost, reconnecting")
        else:
            self.state.changed()  # a light/group/scene changed

    async def run(self) -> None:
        """Connect, and reconnect with backoff if the initial connection fails."""
        delay = 2
        while True:
            bridge = HueBridgeV2(self.host, self.app_key)
            try:
                await bridge.initialize()
            except Unauthorized:
                await bridge.close()
                log.error("bridge %s rejected the app key; run `hue-palace pair`", self.host)
                self._set_connected(False, "app key rejected (unauthorized)")
                await asyncio.sleep(60)
                continue
            except Exception as err:  # noqa: BLE001 - bridge offline, DNS, TLS...
                await bridge.close()
                log.warning("cannot connect to bridge %s: %s (retry in %ss)", self.host, err, delay)
                self._set_connected(False, f"cannot connect: {err or type(err).__name__}")
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60)
                continue

            delay = 2
            self.bridge = bridge
            self.state.bridge.bridge_id = bridge.bridge_id
            log.info("connected to bridge %s (%s)", self.host, bridge.bridge_id)
            config = self.state.config
            remotes = [(kb.name, kb.keys) for kb in config.keyboards]
            remotes += [(b.name, b.mappings()) for b in config.ble_buttons]
            for name, mapping in remotes:
                for key, action in mapping.items():
                    if problem := self.check(action):
                        log.warning("%s / %s: %s", name, key, problem)
            unsubscribe = bridge.subscribe(self._on_event)
            unsubscribe_events = bridge.events.subscribe(
                self._on_event,
                (EventType.CONNECTED, EventType.RECONNECTED, EventType.DISCONNECTED),
            )
            self._set_connected(True)
            try:
                await self._worker()
            finally:
                unsubscribe()
                unsubscribe_events()
                self.bridge = None
                self._set_connected(False, "stopped")
                await bridge.close()

    async def _worker(self) -> None:
        while True:
            action, entry = await self._queue.get()
            try:
                if not self.ready:
                    raise ResolveError("bridge not connected")
                result = await execute(self.bridge, action)
            except ResolveError as err:
                log.warning("%s: %s", action.describe(), err)
                self.state.finish(entry, False, str(err))
            except Exception as err:  # noqa: BLE001 - keep the worker alive
                log.exception("%s failed", action.describe())
                self.state.finish(entry, False, f"{type(err).__name__}: {err}")
            else:
                log.info("%s: %s", action.describe(), result)
                self.state.finish(entry, True, result)

    # -- public API ---------------------------------------------------------------

    def submit(self, action: Action, entry: LogEntry) -> None:
        """Queue an action (called from key readers). Never blocks."""
        if not self.ready:
            self.state.finish(entry, False, "bridge not connected")
            return
        try:
            self._queue.put_nowait((action, entry))
        except asyncio.QueueFull:
            self.state.finish(entry, False, "busy: too many queued commands, dropped")

    def check(self, action: Action) -> str | None:
        """Return a problem description if the action can't be resolved right now."""
        if self.bridge is None:
            return None  # unknown until connected
        try:
            resolve(self.bridge, action)
        except ResolveError as err:
            return str(err)
        return None

    def snapshot(self) -> dict[str, Any]:
        bridge = self.bridge
        if bridge is None:
            return {"rooms": [], "lights": []}
        groups = []
        for kind, controller in (("room", bridge.groups.room), ("zone", bridge.groups.zone)):
            for group in controller:
                grouped = bridge.groups.grouped_light.get(group.grouped_light) if group.grouped_light else None
                on = bool(grouped and grouped.on and grouped.on.on)
                brightness = grouped.dimming.brightness if grouped and grouped.dimming else None
                groups.append(
                    {"kind": kind, "name": _name(group), "on": on,
                     "brightness": round(brightness) if on and brightness is not None else None}
                )
        lights = [
            {"name": _name(light), "on": light.is_on,
             "brightness": round(light.brightness) if light.is_on else None}
            for light in bridge.lights
        ]
        groups.sort(key=lambda g: (g["kind"], g["name"].casefold()))
        lights.sort(key=lambda light: light["name"].casefold())
        return {"rooms": groups, "lights": lights}
