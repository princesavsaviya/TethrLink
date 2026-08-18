"""Wire format for client → server input, and coordinate scaling.

Framing is `type:u8 length:u8 payload[length]`. The explicit length is what
makes an unrecognised message *skippable* rather than fatal, so adding message
types later never desynchronises an older peer.

Coordinates arrive normalised to [0,1] in video space. Only the client knows
its own rendering geometry — H.264 fills the panel while JPEG letterboxes —
so normalising there keeps codec-specific knowledge in the renderer and keeps
this format resolution-independent.

No `gi`/`dbus` import: this is the logic that has to be right, so it is kept
testable without a GNOME session.
"""

import math
import struct
from dataclasses import dataclass
from typing import List, Optional, Tuple

MSG_POINTER_MOTION = 0x01
MSG_POINTER_BUTTON = 0x02
MSG_POINTER_AXIS = 0x03

_HEADER = 2  # type + length

# Wire enum → evdev button code. The wire carries a small index rather than
# evdev constants so a Linux implementation detail does not leak into a
# protocol an Android client also has to speak.
BUTTON_CODES = {
    0: 0x110,  # BTN_LEFT
    1: 0x111,  # BTN_RIGHT
    2: 0x112,  # BTN_MIDDLE
}


@dataclass(frozen=True)
class InputMessage:
    type: int
    payload: bytes


def parse_messages(buffer: bytes) -> Tuple[List[InputMessage], bytes]:
    """Split a byte buffer into complete messages plus any trailing remainder.

    TCP delivers arbitrary fragments, so the remainder must be carried into
    the next read rather than discarded.
    """
    messages: List[InputMessage] = []
    offset = 0
    while len(buffer) - offset >= _HEADER:
        msg_type = buffer[offset]
        length = buffer[offset + 1]
        end = offset + _HEADER + length
        if len(buffer) < end:
            break  # incomplete; wait for more bytes
        messages.append(
            InputMessage(type=msg_type, payload=buffer[offset + _HEADER:end])
        )
        offset = end
    return messages, buffer[offset:]


def decode_motion(payload: bytes) -> Optional[Tuple[float, float]]:
    if len(payload) != 8:
        return None
    return struct.unpack(">ff", payload)


def decode_button(payload: bytes) -> Optional[Tuple[int, bool]]:
    if len(payload) != 2:
        return None
    code = BUTTON_CODES.get(payload[0])
    if code is None:
        return None
    return code, bool(payload[1])


def decode_axis(payload: bytes) -> Optional[Tuple[float, float]]:
    if len(payload) != 8:
        return None
    return struct.unpack(">ff", payload)


def to_stream_coords(
    nx: float, ny: float, width: int, height: int
) -> Optional[Tuple[float, float]]:
    """Normalised [0,1] → pixel coordinates within the ScreenCast stream.

    Values are clamped rather than trusted: the client is remote input, and a
    malformed value must not send the pointer somewhere unexpected. Non-finite
    values are rejected outright, since clamping a NaN silently produces a
    plausible-looking coordinate.
    """
    if width <= 0 or height <= 0:
        return None
    if not (math.isfinite(nx) and math.isfinite(ny)):
        return None
    cx = min(1.0, max(0.0, nx))
    cy = min(1.0, max(0.0, ny))
    return cx * width, cy * height
