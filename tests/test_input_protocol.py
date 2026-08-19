import struct

from server.core.input_protocol import (
    BUTTON_CODES,
    MSG_POINTER_AXIS,
    MSG_POINTER_BUTTON,
    MSG_POINTER_MOTION,
    decode_axis,
    decode_button,
    decode_motion,
    parse_messages,
    to_stream_coords,
)


def _frame(msg_type, payload):
    return bytes([msg_type, len(payload)]) + payload


# ── framing ──────────────────────────────────────────────────────────────────

def test_parses_a_single_message():
    payload = struct.pack(">ff", 0.5, 0.25)
    msgs, rest = parse_messages(_frame(MSG_POINTER_MOTION, payload))
    assert len(msgs) == 1
    assert msgs[0].type == MSG_POINTER_MOTION
    assert msgs[0].payload == payload
    assert rest == b""


def test_parses_several_messages_in_one_buffer():
    buf = (_frame(MSG_POINTER_MOTION, struct.pack(">ff", 0.1, 0.2))
           + _frame(MSG_POINTER_BUTTON, bytes([0, 1])))
    msgs, rest = parse_messages(buf)
    assert [m.type for m in msgs] == [MSG_POINTER_MOTION, MSG_POINTER_BUTTON]
    assert rest == b""


def test_keeps_a_partial_message_for_the_next_read():
    """TCP delivers arbitrary fragments; a split message must not be lost."""
    full = _frame(MSG_POINTER_MOTION, struct.pack(">ff", 0.1, 0.2))
    msgs, rest = parse_messages(full[:5])
    assert msgs == []
    assert rest == full[:5]

    msgs, rest = parse_messages(rest + full[5:])
    assert len(msgs) == 1
    assert rest == b""


def test_empty_buffer_yields_nothing():
    assert parse_messages(b"") == ([], b"")


def test_unknown_message_type_is_skipped_not_fatal():
    """The length byte exists so an unrecognised type can be stepped over."""
    buf = (_frame(0x7F, b"\x01\x02\x03")
           + _frame(MSG_POINTER_BUTTON, bytes([0, 1])))
    msgs, rest = parse_messages(buf)
    assert [m.type for m in msgs] == [0x7F, MSG_POINTER_BUTTON]
    assert rest == b""


def test_zero_length_payload_is_handled():
    msgs, rest = parse_messages(_frame(0x40, b""))
    assert len(msgs) == 1 and msgs[0].payload == b""
    assert rest == b""


# ── payload decoding ─────────────────────────────────────────────────────────

def test_decodes_motion():
    x, y = decode_motion(struct.pack(">ff", 0.25, 0.75))
    assert abs(x - 0.25) < 1e-6
    assert abs(y - 0.75) < 1e-6


def test_motion_with_wrong_length_is_rejected():
    assert decode_motion(b"\x00\x00") is None
    assert decode_motion(b"") is None


def test_decodes_button():
    code, pressed = decode_button(bytes([0, 1]))
    assert code == BUTTON_CODES[0]
    assert pressed is True

    code, pressed = decode_button(bytes([1, 0]))
    assert code == BUTTON_CODES[1]
    assert pressed is False


def test_unknown_button_index_is_rejected():
    assert decode_button(bytes([99, 1])) is None


def test_button_with_wrong_length_is_rejected():
    assert decode_button(b"\x00") is None


def test_left_and_right_map_to_distinct_evdev_codes():
    assert BUTTON_CODES[0] != BUTTON_CODES[1]
    # evdev BTN_LEFT / BTN_RIGHT
    assert BUTTON_CODES[0] == 0x110
    assert BUTTON_CODES[1] == 0x111


def test_decodes_axis():
    dx, dy = decode_axis(struct.pack(">ff", -1.5, 2.0))
    assert abs(dx + 1.5) < 1e-6
    assert abs(dy - 2.0) < 1e-6


# ── coordinate scaling ───────────────────────────────────────────────────────

def test_scales_normalised_coordinates_to_stream_pixels():
    assert to_stream_coords(0.0, 0.0, 1730, 1080) == (0.0, 0.0)
    x, y = to_stream_coords(1.0, 1.0, 1730, 1080)
    assert abs(x - 1730) < 1e-6 and abs(y - 1080) < 1e-6
    x, y = to_stream_coords(0.5, 0.5, 1730, 1080)
    assert abs(x - 865) < 1e-6 and abs(y - 540) < 1e-6


def test_slightly_out_of_range_values_are_clamped():
    """Defence against a malformed client, not an expected path."""
    x, y = to_stream_coords(1.2, -0.3, 1730, 1080)
    assert x == 1730.0
    assert y == 0.0


def test_non_finite_coordinates_are_rejected():
    assert to_stream_coords(float("nan"), 0.5, 1730, 1080) is None
    assert to_stream_coords(0.5, float("inf"), 1730, 1080) is None


def test_zero_sized_stream_is_rejected():
    assert to_stream_coords(0.5, 0.5, 0, 1080) is None
