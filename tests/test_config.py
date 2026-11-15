from pathlib import Path

import pytest

from hue_palace.config import ActionType, ConfigError, load_config, parse_config

EXAMPLE = Path(__file__).parent.parent / "config.example.yaml"


def base(**keys):
    return {
        "bridge": {"host": "1.2.3.4", "app_key": "k"},
        "keyboards": [{"name": "kb", "match": {"name": "Kbd"}, "keys": keys}],
    }


def test_example_config_is_valid():
    config = load_config(EXAMPLE)
    assert len(config.keyboards) == 2
    assert config.keyboards[0].match.mac == "aa:bb:cc:dd:ee:ff"  # normalised
    assert config.keyboards[0].keys["KEY_UP"].step == 15


@pytest.mark.parametrize(
    "action, message",
    [
        ({"action": "scene"}, "needs a 'scene'"),
        ({"action": "scene", "scene": "x", "light": "l"}, "not 'light'"),
        ({"action": "toggle"}, "exactly one of"),
        ({"action": "on", "room": "a", "light": "b"}, "exactly one of"),
        ({"action": "off", "room": "a", "scene": "s"}, "does not take a 'scene'"),
        ({"action": "explode", "room": "a"}, "action"),
        ({"action": "on", "room": "a", "colour": "red"}, "colour"),
        ({"action": "brightness_up", "room": "a", "step": 0}, "step"),
    ],
)
def test_invalid_actions(action, message):
    with pytest.raises(ConfigError, match=message):
        parse_config(base(KEY_A=action))


def test_unknown_key_code():
    with pytest.raises(ConfigError, match="KEY_NOPE"):
        parse_config(base(KEY_NOPE={"action": "on", "room": "a"}))


def test_match_needs_mac_or_name():
    raw = base(KEY_A={"action": "on", "room": "a"})
    raw["keyboards"][0]["match"] = {}
    with pytest.raises(ConfigError, match="mac"):
        parse_config(raw)


def test_duplicate_keyboard_names():
    raw = base(KEY_A={"action": "on", "room": "a"})
    raw["keyboards"].append(raw["keyboards"][0])
    with pytest.raises(ConfigError, match="duplicate"):
        parse_config(raw)


def test_describe():
    c = parse_config(base(
        KEY_A={"action": "scene", "scene": "Relax", "room": "Den"},
        KEY_B={"action": "brightness_down", "zone": "Up", "step": 20},
    ))
    keys = c.keyboards[0].keys
    assert keys["KEY_A"].action is ActionType.SCENE
    assert keys["KEY_A"].describe() == "scene 'Relax' (room Den)"
    assert keys["KEY_B"].describe() == "brightness down 20% zone 'Up'"


def test_app_key_sources(tmp_path, monkeypatch):
    keyfile = tmp_path / "appkey"
    keyfile.write_text("from-file\n")
    raw = base(KEY_A={"action": "on", "room": "a"})
    raw["bridge"] = {"host": "h", "app_key_file": str(keyfile)}
    monkeypatch.delenv("HUE_APP_KEY", raising=False)
    assert parse_config(raw).bridge.resolve_app_key() == "from-file"
    monkeypatch.setenv("HUE_APP_KEY", "from-env")
    assert parse_config(raw).bridge.resolve_app_key() == "from-env"


def test_bridge_host_from_env(monkeypatch):
    monkeypatch.setenv("HUE_BRIDGE_HOST", "10.0.0.2")
    assert parse_config(base(KEY_A={"action": "on", "room": "a"})).bridge.host == "10.0.0.2"


@pytest.mark.parametrize(
    "match, name, uniq, expected",
    [
        ({"mac": "AA:BB"}, "whatever", "aa:bb", True),
        ({"mac": "AA:BB"}, "whatever", "cc:dd", False),
        ({"name": "Kbd"}, "Kbd", "", True),
        ({"name": "Kbd"}, "Kbd Mouse", "", False),
        ({"name": "kbd", "name_contains": True}, "BT Kbd Mouse", "", True),
        ({"mac": "AA:BB", "name": "Kbd"}, "Kbd", "cc:dd", False),
    ],
)
def test_device_match(match, name, uniq, expected):
    raw = base(KEY_A={"action": "on", "room": "a"})
    raw["keyboards"][0]["match"] = match
    assert parse_config(raw).keyboards[0].match.matches(name, uniq) is expected
