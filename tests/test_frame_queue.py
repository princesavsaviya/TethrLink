import threading

from server.core.frame_queue import EncodedFrameQueue, Frame, LatestFrameSlot
from server.core.metrics import StreamMetrics


# ── EncodedFrameQueue: the H.264 path ────────────────────────────────────────

def test_frames_come_back_in_order():
    q = EncodedFrameQueue(maxsize=4)
    q.put(b"a")
    q.put(b"b")
    q.put(b"c")
    assert [q.get(0.1).data for _ in range(3)] == [b"a", b"b", b"c"]


def test_never_returns_the_same_frame_twice():
    """The single-slot bug: a second read returned the same access unit again."""
    q = EncodedFrameQueue(maxsize=4)
    q.put(b"only")
    assert q.get(0.1).data == b"only"
    assert q.get(0.05) is None


def test_get_returns_none_on_timeout_when_empty():
    q = EncodedFrameQueue(maxsize=4)
    assert q.get(0.05) is None


def test_sequence_numbers_are_monotonic():
    q = EncodedFrameQueue(maxsize=4)
    q.put(b"a")
    q.put(b"b")
    assert [q.get(0.1).seq for _ in range(2)] == [0, 1]


def test_keyframe_flag_is_preserved():
    q = EncodedFrameQueue(maxsize=4)
    q.put(b"idr", is_keyframe=True)
    q.put(b"delta", is_keyframe=False)
    assert q.get(0.1).is_keyframe is True
    assert q.get(0.1).is_keyframe is False


def test_fills_to_capacity_without_dropping():
    """No silent single-frame drop: every frame up to maxsize survives."""
    q = EncodedFrameQueue(maxsize=3)
    assert q.put(b"a") is True
    assert q.put(b"b") is True
    assert q.put(b"c") is True
    assert [q.get(0.1).data for _ in range(3)] == [b"a", b"b", b"c"]


def test_overflow_drains_queue_and_reports_failure():
    """Overflow must not drop one frame in isolation — that breaks the
    reference chain. It drains and demands a resync."""
    q = EncodedFrameQueue(maxsize=2)
    q.put(b"a")
    q.put(b"b")
    assert q.put(b"c") is False
    assert q.get(0.05) is None


def test_overflow_invokes_callback_once():
    calls = []
    q = EncodedFrameQueue(maxsize=1, on_overflow=lambda: calls.append(1))
    q.put(b"a")
    q.put(b"b")
    assert len(calls) == 1


def test_overflow_increments_metrics():
    m = StreamMetrics()
    q = EncodedFrameQueue(maxsize=1, metrics=m)
    q.put(b"a")
    q.put(b"b")
    snap = m.snapshot()
    assert snap["queue_overflows"] == 1
    assert snap["frames_dropped"] == 1


def test_put_counts_encoded_frames():
    m = StreamMetrics()
    q = EncodedFrameQueue(maxsize=4, metrics=m)
    q.put(b"a")
    q.put(b"b")
    assert m.snapshot()["frames_encoded"] == 2


def test_drain_empties_and_returns_count():
    q = EncodedFrameQueue(maxsize=4)
    q.put(b"a")
    q.put(b"b")
    assert q.drain() == 2
    assert q.get(0.05) is None


def test_get_unblocks_when_a_producer_arrives():
    q = EncodedFrameQueue(maxsize=4)
    result = []

    def consume():
        result.append(q.get(2.0))

    t = threading.Thread(target=consume)
    t.start()
    q.put(b"late")
    t.join(timeout=3)
    assert result[0].data == b"late"


def test_close_releases_a_blocked_consumer():
    q = EncodedFrameQueue(maxsize=4)
    result = []

    def consume():
        result.append(q.get(5.0))

    t = threading.Thread(target=consume)
    t.start()
    q.close()
    t.join(timeout=3)
    assert result == [None]


# ── LatestFrameSlot: the JPEG path ───────────────────────────────────────────

def test_slot_returns_the_latest_value():
    slot = LatestFrameSlot()
    slot.put(b"old")
    slot.put(b"new")
    assert slot.get() == b"new"


def test_slot_returns_none_when_nothing_new():
    """Prevents retransmitting an identical JPEG."""
    slot = LatestFrameSlot()
    slot.put(b"a")
    assert slot.get() == b"a"
    assert slot.get() is None


def test_slot_counts_suppressed_duplicates():
    m = StreamMetrics()
    slot = LatestFrameSlot(metrics=m)
    slot.put(b"a")
    slot.get()
    slot.get()
    slot.get()
    assert m.snapshot()["duplicates_suppressed"] == 2


def test_slot_counts_overwritten_frames_as_dropped():
    m = StreamMetrics()
    slot = LatestFrameSlot(metrics=m)
    slot.put(b"a")
    slot.put(b"b")
    assert m.snapshot()["frames_dropped"] == 1
