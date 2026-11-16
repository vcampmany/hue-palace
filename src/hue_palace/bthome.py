"""Decoder for BTHome v2 BLE advertisements (https://bthome.io/format/).

Only what hue-palace needs is interpreted (packet id, battery, button events); every
other object is skipped by its size. Pure functions, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field

SERVICE_UUID = "0000fcd2-0000-1000-8000-00805f9b34fb"
UUID_BYTES = b"\xd2\xfc"  # 0xFCD2, little-endian as transmitted

BUTTON_EVENTS = {
    0x01: "press",
    0x02: "double_press",
    0x03: "triple_press",
    0x04: "long_press",
    0x05: "long_double_press",
    0x06: "long_triple_press",
    0x80: "hold_press",
    0x81: "hold_release",
}
EVENT_NAMES = tuple(BUTTON_EVENTS.values())

OBJ_PACKET_ID = 0x00
OBJ_BATTERY = 0x01
OBJ_BUTTON = 0x3A
_VARIABLE = {0x3B, 0x53, 0x54}  # length byte follows the object id

# Data size in bytes of each fixed-size object id.
OBJECT_SIZES: dict[int, int] = {
    0x00: 1, 0x01: 1, 0x02: 2, 0x03: 2, 0x04: 3, 0x05: 3, 0x06: 2, 0x07: 2, 0x08: 2, 0x09: 1,
    0x0A: 3, 0x0B: 3, 0x0C: 2, 0x0D: 2, 0x0E: 2, 0x12: 2, 0x13: 2, 0x14: 2,
    **{i: 1 for i in range(0x0F, 0x12)},
    **{i: 1 for i in range(0x15, 0x30)},
    0x3A: 1, 0x3C: 2, 0x3D: 2, 0x3E: 4, 0x3F: 2, 0x40: 2, 0x41: 2, 0x42: 3, 0x43: 2, 0x44: 2,
    0x45: 2, 0x46: 1, 0x47: 2, 0x48: 2, 0x49: 2, 0x4A: 2, 0x4B: 3, 0x4C: 4, 0x4D: 4, 0x4E: 4,
    0x4F: 4, 0x50: 4, 0x51: 2, 0x52: 2, 0x55: 4, 0x56: 2, 0x57: 1, 0x58: 1, 0x59: 1, 0x5A: 2,
    0x5B: 4, 0x5C: 4, 0x5D: 2, 0x5E: 2, 0x5F: 2, 0x60: 1, 0x61: 2, 0x62: 4, 0x63: 4, 0x64: 1,
    0x65: 1, 0xF0: 2, 0xF1: 4, 0xF2: 3,
}


class BTHomeError(Exception):
    """Not a valid BTHome v2 packet, or it couldn't be decrypted."""


@dataclass
class Packet:
    encrypted: bool
    payload: bytes  # decrypted object bytes, used for de-duplication
    packet_id: int | None = None
    counter: int | None = None  # encryption counter
    battery: int | None = None
    # One entry per 0x3A object, in order (index 0 = button 1). None = no event.
    buttons: list[str | None] = field(default_factory=list)

    @property
    def events(self) -> list[tuple[int, str]]:
        """(button number starting at 1, event name) for every button with an event."""
        return [(i + 1, e) for i, e in enumerate(self.buttons) if e]


def mac_bytes(mac: str) -> bytes:
    return bytes.fromhex(mac.replace(":", "").replace("-", ""))


def decrypt(data: bytes, mac: str, bindkey: bytes) -> tuple[bytes, int]:
    """Decrypt service data (without the UUID). Returns (plaintext objects, counter)."""
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESCCM

    if len(data) < 1 + 4 + 4:
        raise BTHomeError("encrypted packet too short")
    info, ciphertext, counter_bytes, mic = data[0:1], data[1:-8], data[-8:-4], data[-4:]
    nonce = mac_bytes(mac) + UUID_BYTES + info + counter_bytes
    try:
        plain = AESCCM(bindkey, tag_length=4).decrypt(nonce, ciphertext + mic, None)
    except InvalidTag as err:
        raise BTHomeError("decryption failed (wrong bindkey?)") from err
    return plain, int.from_bytes(counter_bytes, "little")


def parse(data: bytes, mac: str, bindkey: bytes | None = None) -> Packet:
    """Parse BTHome service data (the bytes stored under UUID 0xFCD2)."""
    if not data:
        raise BTHomeError("empty packet")
    info = data[0]
    if info >> 5 != 2:
        raise BTHomeError(f"unsupported BTHome version {info >> 5}")
    encrypted = bool(info & 0x01)
    counter = None
    if encrypted:
        if bindkey is None:
            return Packet(encrypted=True, payload=bytes(data))
        objects, counter = decrypt(bytes(data), mac, bindkey)
    else:
        objects = bytes(data[1:])

    packet = Packet(encrypted=encrypted, payload=objects, counter=counter)
    i = 0
    while i < len(objects):
        obj = objects[i]
        i += 1
        if obj in _VARIABLE:
            if i >= len(objects):
                break
            size = objects[i] & (0x1F if obj == 0x3B else 0xFF)
            i += 1
        elif obj in OBJECT_SIZES:
            size = OBJECT_SIZES[obj]
        else:
            break  # spec: stop at the first unknown object id
        value = objects[i:i + size]
        if len(value) < size:
            break  # truncated
        i += size
        if obj == OBJ_PACKET_ID:
            packet.packet_id = value[0]
        elif obj == OBJ_BATTERY:
            packet.battery = value[0]
        elif obj == OBJ_BUTTON:
            packet.buttons.append(BUTTON_EVENTS.get(value[0]))
    return packet
