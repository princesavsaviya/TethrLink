"""Frame-accessor behaviour for BOTH codec configurations.

The single-slot fix introduced a second accessor (`get_encoded_frame`) for the
inter-frame path but left a third call site — the pre-handshake dimension wait
— calling `get_frame()`, which raised TypeError on every H.264 connection while
JPEG kept working. These tests pin down both codecs' accessors so a future
change cannot break one of them while the other still works.

The capture objects here are real `PipeWireCapture` instances constructed
without `__init__` (no GStreamer pipeline, no PipeWire node) so that the real
accessor methods and the real frame buffers are exercised. Anything requiring a
live pipeline or socket is out of reach of unit tests and is not faked here.
"""

import threading

import pytest

gi = pytest.importorskip("gi")  # server_core needs GStreamer bindings to import

from server.core.frame_queue import EncodedFrameQueue, LatestFrameSlot
from server.core.metrics import StreamMetrics
from server.core.server_core import (  # noqa: E402
    CODEC_H264,
    CODEC_JPEG,
    PipeWireCapture,
    X11MssCapture,
    resolve_stream_dimensions,
)


CONFIGURED_W, CONFIGURED_H = 1920, 1080
# The H.264 pipeline scales, so the encoded frames are smaller than the
# requested display size — this is exactly why the handshake must report the
# dimensions observed on the sample caps rather than the configured ones.
SCALED_W, SCALED_H = 1280, 720


def _bare_capture(codec):
    cap = object.__new__(PipeWireCapture)
    cap.width = CONFIGURED_W
    cap.height = CONFIGURED_H
    cap.codec = codec
    cap._lock = threading.Lock()
    cap.metrics = StreamMetrics()
    cap.is_inter_frame = (codec == CODEC_H264)
    cap._first_sample = threading.Event()
    return cap


def h264_capture(observed=True):
    """A capture in the H.264 configuration, backed by a real lossless queue."""
    cap = _bare_capture(CODEC_H264)
    cap._sink_buffer = EncodedFrameQueue(maxsize=4, metrics=cap.metrics)
    if observed:
        # What _on_sample does for every sample: cache the caps dimensions and
        # record that a sample has been seen.
        cap._fw, cap._fh = SCALED_W, SCALED_H
        cap._first_sample.set()
    else:
        cap._fw, cap._fh = CONFIGURED_W, CONFIGURED_H
    return cap


def jpeg_capture(observed=True):
    """A capture in the JPEG configuration, backed by a real latest-wins slot."""
    cap = _bare_capture(CODEC_JPEG)
    cap._sink_buffer = LatestFrameSlot(metrics=cap.metrics)
    cap._fw, cap._fh = CONFIGURED_W, CONFIGURED_H
    if observed:
        cap._sink_buffer.put(b"jpeg-frame")
        cap._first_sample.set()
    return cap


# ── The regression that made every H.264 connection fail ─────────────────────

def test_h264_dimension_wait_does_not_raise():
    """The bug: this path called get_frame(), which called
    EncodedFrameQueue.get() with no timeout → TypeError → client dropped."""
    cap = h264_capture()
    assert resolve_stream_dimensions(cap, attempts=2, sleep_s=0.01) == (
        SCALED_W, SCALED_H
    )


def test_h264_dimension_wait_does_not_consume_the_first_access_unit():
    """The first access unit carries SPS/PPS/IDR. If the handshake popped it,
    the client's decoder could never initialise."""
    cap = h264_capture()
    cap._sink_buffer.put(b"sps-pps-idr", is_keyframe=True)
    cap._sink_buffer.put(b"delta-1")

    assert resolve_stream_dimensions(cap, attempts=2, sleep_s=0.01) == (
        SCALED_W, SCALED_H
    )

    # Both frames must still be queued, in order, with the IDR first.
    first = cap.get_encoded_frame(timeout=0.1)
    assert first == (b"sps-pps-idr", SCALED_W, SCALED_H)
    assert cap.get_encoded_frame(timeout=0.1)[0] == b"delta-1"


def test_h264_dimension_wait_works_before_any_frame_is_queued():
    """Dimensions come from the caps, not from a queued frame: a sample that
    has been observed is enough even if the send loop already drained it."""
    cap = h264_capture()
    assert resolve_stream_dimensions(cap, attempts=2, sleep_s=0.01) == (
        SCALED_W, SCALED_H
    )
    assert cap.get_encoded_frame(timeout=0.01) is None


def test_h264_dimension_wait_falls_back_to_configured_dimensions():
    cap = h264_capture(observed=False)  # no sample ever arrives
    assert resolve_stream_dimensions(cap, attempts=2, sleep_s=0.01) == (
        CONFIGURED_W, CONFIGURED_H
    )


def test_h264_get_frame_is_refused_instead_of_silently_corrupting():
    """get_frame() is JPEG-only. On H.264 it must fail loudly and, crucially,
    must not pop from the lossless queue."""
    cap = h264_capture()
    cap._sink_buffer.put(b"sps-pps-idr", is_keyframe=True)
    with pytest.raises(RuntimeError, match="JPEG-only"):
        cap.get_frame()
    assert cap.get_encoded_frame(timeout=0.1)[0] == b"sps-pps-idr"


def test_h264_encoded_accessor_returns_observed_dimensions():
    cap = h264_capture()
    cap._sink_buffer.put(b"au", is_keyframe=True)
    assert cap.get_encoded_frame(timeout=0.1) == (b"au", SCALED_W, SCALED_H)


# ── Idle-source keepalive: cached IDR + keyframe gate ────────────────────────
#
# The stream used to die outright when the virtual display was idle: PipeWire
# only delivers on damage, so the encoder got no input, `frames_encoded` froze,
# and the old keepalive's keyframe request could not help (there is no frame to
# encode). The send loop now retransmits the cached IDR instead. Only the
# cache/accessor/gate half of that is reachable without GStreamer, which is
# what these tests pin down; the socket write itself is not faked.

def _feed_sample(cap, data, is_key):
    """Mirror the two side effects `_on_sample` has on an H.264 capture:
    cache the access unit when it is a keyframe, then enqueue it."""
    if is_key:
        with cap._lock:
            cap._last_keyframe = data
    return cap._sink_buffer.put(data, is_keyframe=is_key)


def test_last_keyframe_is_none_before_anything_is_encoded():
    """The 'nothing has ever been encoded' case the send loop must handle:
    there is nothing to retransmit, so only a keyframe request is available."""
    assert h264_capture(observed=False).last_keyframe() is None


def test_last_keyframe_caches_the_most_recent_idr():
    cap = h264_capture()
    _feed_sample(cap, b"idr-1", True)
    assert cap.last_keyframe() == b"idr-1"
    _feed_sample(cap, b"idr-2", True)
    assert cap.last_keyframe() == b"idr-2"


def test_delta_frames_do_not_replace_the_cached_keyframe():
    """Retransmitting a delta frame would corrupt decoder state, so a delta
    must never end up in the cache the keepalive retransmits from."""
    cap = h264_capture()
    _feed_sample(cap, b"idr", True)
    _feed_sample(cap, b"delta", False)
    assert cap.last_keyframe() == b"idr"


def test_last_keyframe_does_not_consume_the_queued_frame():
    """The cache is a copy, not a peek into the queue: the send loop must
    still receive the IDR through the normal path."""
    cap = h264_capture()
    _feed_sample(cap, b"idr", True)
    assert cap.last_keyframe() == b"idr"
    assert cap.get_encoded_frame(timeout=0.1) == (b"idr", SCALED_W, SCALED_H)


def test_require_keyframe_gate_arms_the_queues_gate():
    cap = h264_capture()
    _feed_sample(cap, b"idr", True)
    assert cap._sink_buffer.needs_keyframe is False
    cap.require_keyframe_gate()
    assert cap._sink_buffer.needs_keyframe is True


def test_idle_retransmission_rejects_deltas_until_a_fresh_idr():
    """The whole invariant, end to end at capture level: after the keepalive
    retransmits the cached IDR the decoder sits on that IDR while the encoder's
    reference state is elsewhere, so resumed delta frames must be rejected
    until a fresh IDR resyncs both ends."""
    cap = h264_capture()
    _feed_sample(cap, b"idr", True)
    assert cap.get_encoded_frame(timeout=0.1)[0] == b"idr"

    # Source goes idle; the send loop retransmits what last_keyframe() gives it
    # and arms the gate.
    retransmitted = cap.last_keyframe()
    assert retransmitted == b"idr"
    cap.require_keyframe_gate()

    # Frames resume with a delta: unsendable, dropped explicitly.
    assert _feed_sample(cap, b"delta", False) is False
    assert cap.get_encoded_frame(timeout=0.05) is None
    assert cap.metrics.snapshot()["frames_dropped"] == 1

    # The requested fresh IDR clears the gate and normal service resumes.
    assert _feed_sample(cap, b"idr-2", True) is True
    assert cap._sink_buffer.needs_keyframe is False
    assert cap.get_encoded_frame(timeout=0.1)[0] == b"idr-2"
    assert _feed_sample(cap, b"delta-2", False) is True
    assert cap.get_encoded_frame(timeout=0.1)[0] == b"delta-2"


def test_require_keyframe_gate_is_a_noop_on_the_jpeg_path():
    """JPEG frames are independently decodable; the slot has no gate and must
    not be asked for one."""
    cap = jpeg_capture()
    cap.require_keyframe_gate()
    assert cap.get_frame() == (b"jpeg-frame", CONFIGURED_W, CONFIGURED_H)


# ── The JPEG configuration must keep working identically ─────────────────────

def test_jpeg_dimension_wait_uses_the_frame_accessor():
    cap = jpeg_capture()
    assert resolve_stream_dimensions(cap, attempts=2, sleep_s=0.01) == (
        CONFIGURED_W, CONFIGURED_H
    )


def test_jpeg_dimension_wait_polls_until_a_frame_arrives():
    cap = jpeg_capture(observed=False)
    polls = []

    def fake_sleep(_):
        polls.append(1)
        if len(polls) == 2:
            cap._sink_buffer.put(b"late-jpeg")

    assert resolve_stream_dimensions(
        cap, attempts=5, sleep_s=0.0, sleep=fake_sleep
    ) == (CONFIGURED_W, CONFIGURED_H)
    assert len(polls) == 2  # returned as soon as a frame showed up


def test_jpeg_dimension_wait_falls_back_to_configured_dimensions():
    cap = jpeg_capture(observed=False)
    assert resolve_stream_dimensions(
        cap, attempts=3, sleep_s=0.0, sleep=lambda _: None
    ) == (CONFIGURED_W, CONFIGURED_H)


def test_jpeg_frame_accessor_returns_data_and_dimensions():
    cap = jpeg_capture()
    assert cap.get_frame() == (b"jpeg-frame", CONFIGURED_W, CONFIGURED_H)
    assert cap.get_frame() is None  # latest-wins: no redundant resend


# ── The idle-JPEG-capture regression ──────────────────────────────────────────
# On a static virtual display, PipeWire only emits on damage: after the first
# handful of frames, the encoder may never produce another one for the whole
# session. If the handshake's dimension probe consumes that one frame (the old
# get_frame()-based implementation), the send loop finds the slot empty
# forever, last_sent_payload never gets bootstrapped, and the server sends
# literally nothing — the client times out and reconnect-storms.

def test_jpeg_peek_frame_does_not_consume():
    cap = jpeg_capture()
    assert cap.peek_frame() == (b"jpeg-frame", CONFIGURED_W, CONFIGURED_H)
    # A normal, consuming read afterward must still see it.
    assert cap.get_frame() == (b"jpeg-frame", CONFIGURED_W, CONFIGURED_H)
    assert cap.get_frame() is None  # now genuinely consumed


def test_jpeg_peek_frame_returns_none_when_empty():
    cap = jpeg_capture(observed=False)
    assert cap.peek_frame() is None


def test_resolve_stream_dimensions_leaves_the_only_frame_for_the_send_loop():
    """The actual regression, asserted directly: on an idle capture that
    produces exactly one frame and never another, the handshake's dimension
    probe must not be the thing that consumes it."""
    cap = jpeg_capture()  # exactly one frame ever arrives, as on an idle display

    assert resolve_stream_dimensions(cap, attempts=5, sleep_s=0.0) == (
        CONFIGURED_W, CONFIGURED_H
    )

    # The send loop's very first poll — its only chance, since the capture
    # is idle and nothing else will ever be produced — must still find it.
    assert cap.get_frame() == (b"jpeg-frame", CONFIGURED_W, CONFIGURED_H)


def test_resolve_stream_dimensions_jpeg_polls_via_peek_not_get():
    """Regression guard for the specific mechanism: polling for dimensions
    must not itself be a sequence of destructive reads even when it takes
    several attempts before the first frame shows up."""
    cap = jpeg_capture(observed=False)

    def fake_sleep(_):
        if cap._sink_buffer.peek() is None:
            cap._sink_buffer.put(b"late-jpeg")

    assert resolve_stream_dimensions(
        cap, attempts=5, sleep_s=0.0, sleep=fake_sleep
    ) == (CONFIGURED_W, CONFIGURED_H)
    # Still there for the send loop — the probe above must have peeked, not
    # popped, on every attempt after the frame arrived.
    assert cap.get_frame() == (b"late-jpeg", CONFIGURED_W, CONFIGURED_H)


# ── X11MssCapture: the same intra-only path, a different capture class ───────

def _bare_x11_capture(frame=b"x11-jpeg", width=CONFIGURED_W, height=CONFIGURED_H):
    cap = object.__new__(X11MssCapture)
    cap.width = width
    cap.height = height
    cap._lock = threading.Lock()
    cap.is_inter_frame = False
    cap._frame = frame
    cap.metrics = StreamMetrics()
    return cap


def test_x11_peek_frame_mirrors_get_frame():
    cap = _bare_x11_capture()
    assert cap.peek_frame() == (b"x11-jpeg", CONFIGURED_W, CONFIGURED_H)


def test_x11_peek_frame_does_not_consume():
    """X11MssCapture has no fresh/consumed tracking — get_frame() is already
    non-destructive — but peek_frame() must still not regress that."""
    cap = _bare_x11_capture()
    assert cap.peek_frame() == (b"x11-jpeg", CONFIGURED_W, CONFIGURED_H)
    assert cap.get_frame() == (b"x11-jpeg", CONFIGURED_W, CONFIGURED_H)
    assert cap.peek_frame() == (b"x11-jpeg", CONFIGURED_W, CONFIGURED_H)


def test_x11_peek_frame_returns_none_when_empty():
    cap = _bare_x11_capture(frame=None)
    assert cap.peek_frame() is None


def test_resolve_stream_dimensions_works_on_x11_capture_too():
    cap = _bare_x11_capture()
    assert resolve_stream_dimensions(cap, attempts=2, sleep_s=0.01) == (
        CONFIGURED_W, CONFIGURED_H
    )
    # Not consumed: X11's frame is always "whatever was last grabbed".
    assert cap.get_frame() == (b"x11-jpeg", CONFIGURED_W, CONFIGURED_H)


def test_wait_for_frame_dimensions_reports_rotated_dimensions():
    """set_orientation() transposes the cached dims; the handshake must see
    whatever the cache currently holds."""
    cap = h264_capture()
    with cap._lock:
        cap._fw, cap._fh = SCALED_H, SCALED_W
    assert cap.wait_for_frame_dimensions(0.1) == (SCALED_H, SCALED_W)


def test_wait_for_frame_dimensions_returns_none_without_a_sample():
    cap = h264_capture(observed=False)
    assert cap.wait_for_frame_dimensions(0.01) is None
