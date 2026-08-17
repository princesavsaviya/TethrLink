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
