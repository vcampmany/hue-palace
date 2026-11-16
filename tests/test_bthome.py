import pytest

from hue_palace.bthome import BTHomeError, parse

MAC = "54:48:e6:8f:80:a5"
KEY = bytes.fromhex("231d39c1d7cc1ab1aee224cd096db932")


def test_four_button_press_packet():
    # info 0x44 (v2, trigger-based), packet id 7, battery 98%, 4 buttons: -, double, -, long
    p = parse(bytes.fromhex("44" "0007" "0162" "3a00" "3a02" "3a00" "3a04"), MAC)
    assert not p.encrypted
    assert p.packet_id == 7 and p.battery == 98
    assert p.buttons == [None, "double_press", None, "long_press"]
    assert p.events == [(2, "double_press"), (4, "long_press")]


def test_hold_events_and_device_ids_after_buttons():
    p = parse(bytes.fromhex("40" "3a80" "3a81" "f01600" "f101020304"), MAC)
    assert p.events == [(1, "hold_press"), (2, "hold_release")]


def test_skips_other_objects_by_size():
    # temperature (0x02, 2 bytes), humidity (0x03, 2 bytes), text (0x53, variable), then a button
    p = parse(bytes.fromhex("40" "02ca09" "03bf13" "5303414243" "3a01"), MAC)
    assert p.events == [(1, "press")]


def test_stops_at_unknown_object():
    p = parse(bytes.fromhex("40" "3a01" "3001" "3a01"), MAC)  # 0x30 is not in the table
    assert p.buttons == ["press"]


def test_rejects_other_versions():
    with pytest.raises(BTHomeError, match="version"):
        parse(bytes.fromhex("02" "3a01"), MAC)


def test_bthome_io_encryption_test_vector():
    data = bytes.fromhex("41e445f3c9962b332211006c7c4519")
    p = parse(data, MAC.upper(), KEY)
    assert p.encrypted and p.counter == 0x00112233
    assert p.payload == bytes.fromhex("02ca0903bf13")  # temperature + humidity


def test_encrypted_without_key_is_reported_not_decoded():
    p = parse(bytes.fromhex("41e445f3c9962b332211006c7c4519"), MAC)
    assert p.encrypted and p.buttons == [] and p.counter is None


def test_wrong_key_fails():
    with pytest.raises(BTHomeError, match="bindkey"):
        parse(bytes.fromhex("41e445f3c9962b332211006c7c4519"), MAC, bytes(16))
