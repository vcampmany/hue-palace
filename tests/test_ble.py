"""BleManager and de-duplication, with fake bleak devices/advertisements."""

import asyncio
from types import SimpleNamespace as NS

from cryptography.hazmat.primitives.ciphers.aead import AESCCM

from hue_palace.ble import BleManager, Deduplicator
from hue_palace.bthome import SERVICE_UUID, UUID_BYTES, mac_bytes, parse
from hue_palace.config import parse_config
from hue_palace.state import AppState

MAC = "7c:c6:b6:61:2e:9a"
KEY = "231d39c1d7cc1ab1aee224cd096db932"


def config(**button):
    return parse_config({
        "bridge": {"host": "h", "app_key": "k"},
        "ble_buttons": [{
            "name": "bedroom", "mac": MAC.upper(),
            "buttons": {
                1: {"press": {"action": "toggle", "room": "Bedroom"}},
                2: {"double_press": {"action": "scene", "scene": "Relax"}},
            },
            **button,
        }],
    })


def adv(payload_hex: str, mac=MAC, rssi=-60):
    return (NS(address=mac.upper(), name=None),
            NS(service_data={SERVICE_UUID: bytes.fromhex(payload_hex)}, rssi=rssi, local_name="SBBT"))


def encrypt(objects: bytes, counter: int, mac=MAC, key=KEY) -> str:
    info = b"\x45"
    c = counter.to_bytes(4, "little")
    nonce = mac_bytes(mac) + UUID_BYTES + info + c
    sealed = AESCCM(bytes.fromhex(key), tag_length=4).encrypt(nonce, objects, None)
    return (info + sealed[:-4] + c + sealed[-4:]).hex()


def setup(cfg):
    state = AppState(cfg)
    dispatched = []
    mgr = BleManager(cfg.ble_buttons, state, lambda action, entry: dispatched.append(action))
    return state, mgr, dispatched


def test_press_dispatches_once_despite_repeats():
    state, mgr, dispatched = setup(config())
    for _ in range(5):  # the button repeats each advertisement
        mgr.on_advertisement(*adv("44" "0010" "0164" "3a01" "3a00" "3a00" "3a00"))
    mgr.on_advertisement(*adv("44" "0011" "3a00" "3a02" "3a00" "3a00"))
    mgr.on_advertisement(*adv("44" "0012" "3a00" "3a00" "3a03" "3a00"))  # not mapped
    assert [a.describe() for a in dispatched] == ["toggle room 'Bedroom'", "scene 'Relax'"]
    st = state.ble_buttons["bedroom"]
    assert st.battery == 100 and st.rssi == -60 and st.presses == 3
    assert st.last_key == "button3:triple_press"
    assert [e.key for e in state.log] == ["button1:press", "button2:double_press", "button3:triple_press"]


def test_unknown_bthome_device_is_listed_and_others_ignored():
    state, mgr, dispatched = setup(config())
    mgr.on_advertisement(*adv("40" "3a01", mac="aa:bb:cc:dd:ee:ff"))
    mgr.on_advertisement(NS(address="11:22:33:44:55:66", name="phone"),
                         NS(service_data={}, rssi=-80, local_name=None))
    assert list(state.ble_unassigned) == ["aa:bb:cc:dd:ee:ff"]
    assert state.ble_unassigned["aa:bb:cc:dd:ee:ff"].last_event == "button1:press"
    assert dispatched == []


def test_encrypted_button_needs_key():
    state, mgr, dispatched = setup(config())
    mgr.on_advertisement(*adv(encrypt(bytes.fromhex("3a01"), 1)))
    assert dispatched == []
    assert "bindkey" in state.ble_buttons["bedroom"].error


def test_encrypted_counter_dedups_and_rejects_replays():
    state, mgr, dispatched = setup(config(bindkey=KEY))
    first = encrypt(bytes.fromhex("3a01"), 100)
    mgr.on_advertisement(*adv(first))
    mgr.on_advertisement(*adv(first))  # repeat
    mgr.on_advertisement(*adv(encrypt(bytes.fromhex("3a01"), 99)))  # older counter: replay
    mgr.on_advertisement(*adv(encrypt(bytes.fromhex("3a01"), 101)))
    assert len(dispatched) == 2
    assert state.ble_buttons["bedroom"].encrypted is True
    assert state.ble_buttons["bedroom"].error is None


def test_wrong_key_shows_error():
    state, mgr, dispatched = setup(config(bindkey="00" * 16))
    mgr.on_advertisement(*adv(encrypt(bytes.fromhex("3a01"), 1)))
    assert dispatched == [] and "decryption failed" in state.ble_buttons["bedroom"].error


def test_dedup_without_packet_id_uses_time_window():
    t = [0.0]
    d = Deduplicator(clock=lambda: t[0])
    p = parse(bytes.fromhex("443a01"), MAC)
    assert d.is_new(MAC, p)
    t[0] = 1.0
    assert not d.is_new(MAC, p)   # repeat within the burst
    t[0] = 2.5
    assert not d.is_new(MAC, p)   # still repeating: window counts from the last copy
    t[0] = 5.0
    assert d.is_new(MAC, p)       # a new press of the same button later


async def test_scanner_retries_until_it_starts():
    state, _, _ = setup(config())
    attempts = []

    class FakeScanner:
        async def start(self):
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError("org.bluez.Error.NotReady")

        async def stop(self):
            pass

    mgr = BleManager(state.config.ble_buttons, state, lambda *a: None,
                     scanner_factory=lambda cb, adapter: FakeScanner())
    task = asyncio.create_task(mgr.run())
    await asyncio.sleep(0)
    assert state.ble_scanner["running"] is False and "NotReady" in state.ble_scanner["error"]
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
