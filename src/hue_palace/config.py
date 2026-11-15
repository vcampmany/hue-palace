"""Configuration file loading and validation."""

from __future__ import annotations

import os
from enum import Enum
from pathlib import Path
from typing import Annotated

import yaml
from evdev import ecodes
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ConfigError(Exception):
    """Raised when the configuration file is missing or invalid."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ActionType(str, Enum):
    SCENE = "scene"
    ON = "on"
    OFF = "off"
    TOGGLE = "toggle"
    BRIGHTNESS_UP = "brightness_up"
    BRIGHTNESS_DOWN = "brightness_down"


class Action(_Strict):
    """What a single key does."""

    action: ActionType
    scene: str | None = None
    light: str | None = None
    room: str | None = None
    zone: str | None = None
    step: Annotated[float, Field(gt=0, le=100)] = 10

    @field_validator("action", mode="before")
    @classmethod
    def _yaml_booleans(cls, v: object) -> object:
        # YAML 1.1 reads a bare `on` / `off` as true / false.
        if v is True:
            return "on"
        if v is False:
            return "off"
        return v

    @model_validator(mode="after")
    def _check_targets(self) -> Action:
        targets = [t for t in ("light", "room", "zone") if getattr(self, t) is not None]
        if self.action is ActionType.SCENE:
            if not self.scene:
                raise ValueError("action 'scene' needs a 'scene' name")
            if self.light is not None:
                raise ValueError("action 'scene' can be scoped by 'room' or 'zone', not 'light'")
            if len(targets) > 1:
                raise ValueError("action 'scene' accepts at most one of 'room' / 'zone'")
        else:
            if self.scene is not None:
                raise ValueError(f"action '{self.action.value}' does not take a 'scene'")
            if len(targets) != 1:
                raise ValueError(
                    f"action '{self.action.value}' needs exactly one of 'light', 'room', 'zone'"
                )
        return self

    @property
    def target_kind(self) -> str | None:
        for kind in ("light", "room", "zone"):
            if getattr(self, kind) is not None:
                return kind
        return None

    @property
    def target_name(self) -> str | None:
        kind = self.target_kind
        return getattr(self, kind) if kind else None

    def describe(self) -> str:
        """Short human-readable description, used in logs and the web UI."""
        if self.action is ActionType.SCENE:
            where = f" ({self.target_kind} {self.target_name})" if self.target_kind else ""
            return f"scene '{self.scene}'{where}"
        verb = self.action.value.replace("_", " ")
        if self.action in (ActionType.BRIGHTNESS_UP, ActionType.BRIGHTNESS_DOWN):
            verb = f"{verb} {self.step:g}%"
        return f"{verb} {self.target_kind} '{self.target_name}'"


class DeviceMatch(_Strict):
    """How to recognise a keyboard among the input devices.

    `mac` is compared with the evdev `uniq` field (the Bluetooth address for BT
    devices). `name` is compared with the evdev device name: exact match, or
    substring match when `name_contains` is true.
    """

    mac: str | None = None
    name: str | None = None
    name_contains: bool = False

    @field_validator("mac")
    @classmethod
    def _normalise_mac(cls, v: str | None) -> str | None:
        return v.strip().lower() if v else v

    @model_validator(mode="after")
    def _check_any(self) -> DeviceMatch:
        if not self.mac and not self.name:
            raise ValueError("'match' needs at least one of 'mac' or 'name'")
        return self

    def matches(self, name: str, uniq: str) -> bool:
        if self.mac and (uniq or "").strip().lower() != self.mac:
            return False
        if self.name:
            if self.name_contains:
                return self.name.lower() in (name or "").lower()
            return (name or "") == self.name
        return True


class KeyboardConfig(_Strict):
    name: str
    match: DeviceMatch
    grab: bool = True
    keys: dict[str, Action]

    @field_validator("keys")
    @classmethod
    def _check_keys(cls, v: dict[str, Action]) -> dict[str, Action]:
        if not v:
            raise ValueError("at least one key mapping is required")
        unknown = [k for k in v if k not in ecodes.ecodes or not k.startswith(("KEY_", "BTN_"))]
        if unknown:
            raise ValueError(
                f"unknown key code(s): {', '.join(unknown)} "
                "(use names like KEY_A, KEY_1, KEY_VOLUMEUP; run `hue-palace list-devices --watch` to see them)"
            )
        return v


class BridgeConfig(_Strict):
    host: str
    app_key: str | None = None
    app_key_file: Path | None = None

    def resolve_app_key(self) -> str:
        """App key from HUE_APP_KEY, then `app_key`, then `app_key_file`."""
        if key := os.environ.get("HUE_APP_KEY"):
            return key.strip()
        if self.app_key:
            return self.app_key.strip()
        if self.app_key_file:
            try:
                return self.app_key_file.read_text().strip()
            except OSError as err:
                raise ConfigError(f"cannot read app key file {self.app_key_file}: {err}") from err
        raise ConfigError(
            "no Hue app key: set HUE_APP_KEY, bridge.app_key or bridge.app_key_file "
            "(get one with `hue-palace pair <bridge-host>`)"
        )


class WebConfig(_Strict):
    host: str = "0.0.0.0"
    port: int = 8080


class Config(_Strict):
    bridge: BridgeConfig
    web: WebConfig = WebConfig()
    keyboards: list[KeyboardConfig]

    @field_validator("keyboards")
    @classmethod
    def _unique_names(cls, v: list[KeyboardConfig]) -> list[KeyboardConfig]:
        names = [k.name for k in v]
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            raise ValueError(f"duplicate keyboard name(s): {', '.join(sorted(dupes))}")
        return v


def load_config(path: str | Path) -> Config:
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text())
    except OSError as err:
        raise ConfigError(f"cannot read config file {path}: {err}") from err
    except yaml.YAMLError as err:
        raise ConfigError(f"invalid YAML in {path}: {err}") from err
    return parse_config(raw, source=str(path))


def parse_config(raw: object, source: str = "<config>") -> Config:
    from pydantic import ValidationError

    if not isinstance(raw, dict):
        raise ConfigError(f"{source}: top level must be a mapping")
    # Allow the bridge host to come from the environment (handy for Docker).
    if host := os.environ.get("HUE_BRIDGE_HOST"):
        raw = {**raw, "bridge": {**(raw.get("bridge") or {}), "host": host}}
    try:
        return Config.model_validate(raw)
    except ValidationError as err:
        lines = []
        for e in err.errors():
            loc = ".".join(str(p) for p in e["loc"])
            lines.append(f"  {loc}: {e['msg']}")
        raise ConfigError(f"{source} is invalid:\n" + "\n".join(lines)) from err
