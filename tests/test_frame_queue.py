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


def test_close_releases_consumer_when_queue_is_at_capacity():
    """Verifies that close() releases a blocked consumer even when the queue
    is at capacity. The concurrent-refill race is prevented by the _closed
    flag (set under the lock in close(), checked under the same lock in put()),
    and is covered separately by test_put_returns_false_after_close.
    """
    q = EncodedFrameQueue(maxsize=2)
    assert q.put(b"a") is True
    assert q.put(b"b") is True  # queue is now at capacity
    q.close()

    result = []

    def consume():
        result.append(q.get(5.0))

    t = threading.Thread(target=consume)
    t.start()
    t.join(timeout=1)
    assert not t.is_alive()  # returned well before the 5s timeout
    assert result == [None]


def test_put_returns_false_after_close():
    """close() marks the queue closed so a producer racing with close()
    can never refill it after the sentinel has been enqueued."""
    q = EncodedFrameQueue(maxsize=4)
    q.close()
    assert q.put(b"late") is False
    assert q.get(0.05) is None


# ── Post-overflow keyframe gate ──────────────────────────────────────────────

def test_overflow_arms_the_keyframe_gate():
    q = EncodedFrameQueue(maxsize=1)
    q.put(b"a")
    assert q.needs_keyframe is False
    q.put(b"b")  # overflow
    assert q.needs_keyframe is True


def test_delta_frames_are_rejected_while_waiting_for_the_resync():
    """After an overflow the reference chain is broken, so a delta frame is
    provably undecodable — sending it is the visible corruption itself."""
    q = EncodedFrameQueue(maxsize=1)
    q.put(b"a")
    q.put(b"b")  # overflow drains the backlog and arms the gate
    assert q.put(b"delta", is_keyframe=False) is False
    assert q.get(0.05) is None


def test_keyframe_clears_the_gate_and_is_accepted():
    q = EncodedFrameQueue(maxsize=2)
    q.put(b"a")
    q.put(b"b")
    q.put(b"c")  # overflow
    assert q.put(b"idr", is_keyframe=True) is True
    assert q.needs_keyframe is False
    assert q.get(0.1).data == b"idr"
    # Normal service resumes: deltas after the IDR are accepted again.
    assert q.put(b"delta", is_keyframe=False) is True
    assert q.get(0.1).data == b"delta"


def test_rejected_frames_count_as_dropped():
    m = StreamMetrics()
    q = EncodedFrameQueue(maxsize=1, metrics=m)
    q.put(b"a")
    q.put(b"b")  # overflow: drains 1 → frames_dropped 1
    q.put(b"d1")
    q.put(b"d2")
    q.put(b"d3")
    assert m.snapshot()["frames_dropped"] == 4
    assert m.snapshot()["queue_overflows"] == 1


def test_gated_rejections_do_not_trigger_more_overflows():
    calls = []
    q = EncodedFrameQueue(maxsize=1, on_overflow=lambda: calls.append(1))
    q.put(b"a")
    q.put(b"b")  # the one real overflow
    q.put(b"delta")
    q.put(b"delta")
    assert len(calls) == 1


def test_only_a_keyframe_reopens_the_queue_after_overflow():
    """Ordering guarantee: the first frame the consumer sees after an overflow
    is always a keyframe, never a mid-GOP delta."""
    q = EncodedFrameQueue(maxsize=2)
    q.put(b"a", is_keyframe=True)
    q.put(b"b")
    q.put(b"c")  # overflow
    q.put(b"d")
    q.put(b"e")
    q.put(b"idr", is_keyframe=True)
    q.put(b"f")
    assert [q.get(0.1).data for _ in range(2)] == [b"idr", b"f"]


# ── Gate armed by the idle keepalive, not by an overflow ─────────────────────

def test_require_keyframe_arms_the_gate_without_an_overflow():
    """The send loop retransmits a cached IDR when the source goes idle; the
    decoder's reference state then no longer matches the encoder's, so the
    gate must be armable directly."""
    q = EncodedFrameQueue(maxsize=4)
    q.put(b"idr", is_keyframe=True)
    assert q.needs_keyframe is False
    q.require_keyframe()
    assert q.needs_keyframe is True


def test_require_keyframe_rejects_deltas_until_a_fresh_keyframe():
    m = StreamMetrics()
    q = EncodedFrameQueue(maxsize=4, metrics=m)
    q.put(b"idr", is_keyframe=True)
    assert q.get(0.1).data == b"idr"

    q.require_keyframe()  # idle keepalive retransmitted that IDR
    # Frames resume, but the encoder's reference chain does not match the
    # decoder's, so every delta is unsendable and explicitly dropped.
    assert q.put(b"delta1", is_keyframe=False) is False
    assert q.put(b"delta2", is_keyframe=False) is False
    assert q.get(0.05) is None
    assert m.snapshot()["frames_dropped"] == 2

    # A fresh IDR resyncs both ends and clears the gate.
    assert q.put(b"idr2", is_keyframe=True) is True
    assert q.needs_keyframe is False
    assert q.get(0.1).data == b"idr2"
    assert q.put(b"delta3", is_keyframe=False) is True
    assert q.get(0.1).data == b"delta3"


def test_require_keyframe_does_not_count_as_an_overflow():
    """Arming the gate is not a failure: it must not inflate queue_overflows
    or invoke the overflow callback."""
    m = StreamMetrics()
    calls = []
    q = EncodedFrameQueue(maxsize=4, metrics=m,
                         on_overflow=lambda: calls.append(1))
    q.put(b"idr", is_keyframe=True)
    q.require_keyframe()
    q.put(b"delta")  # gated
    assert m.snapshot()["queue_overflows"] == 0
    assert calls == []


def test_require_keyframe_is_idempotent():
    q = EncodedFrameQueue(maxsize=4)
    q.require_keyframe()
    q.require_keyframe()
    assert q.needs_keyframe is True
    assert q.put(b"idr", is_keyframe=True) is True
    assert q.needs_keyframe is False


def test_require_keyframe_does_not_discard_the_queued_backlog():
    """Unlike an overflow, arming the gate has no reason to drain: the frames
    already queued were produced before the retransmission and are still
    consistent with what the decoder had at that point."""
    q = EncodedFrameQueue(maxsize=4)
    q.put(b"idr", is_keyframe=True)
    q.put(b"delta")
    q.require_keyframe()
    assert [q.get(0.1).data for _ in range(2)] == [b"idr", b"delta"]


# ── frames_encoded honesty ───────────────────────────────────────────────────

def test_frames_encoded_excludes_the_overflowing_frame():
    """A frame that never entered the queue was not 'encoded' for our purposes
    — it is reported as dropped instead."""
    m = StreamMetrics()
    q = EncodedFrameQueue(maxsize=1, metrics=m)
    q.put(b"a")
    q.put(b"b")  # rejected by overflow
    assert m.snapshot()["frames_encoded"] == 1


def test_frames_encoded_excludes_frames_discarded_after_close():
    m = StreamMetrics()
    q = EncodedFrameQueue(maxsize=4, metrics=m)
    q.put(b"a")
    q.close()
    assert q.put(b"b") is False
    assert m.snapshot()["frames_encoded"] == 1


def test_frames_encoded_excludes_gated_rejections():
    m = StreamMetrics()
    q = EncodedFrameQueue(maxsize=1, metrics=m)
    q.put(b"a")   # counted
    q.put(b"b")   # overflow, not counted
    q.put(b"c")   # gated, not counted
    q.put(b"idr", is_keyframe=True)  # counted
    assert m.snapshot()["frames_encoded"] == 2


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


def test_slot_peek_does_not_consume():
    """The handshake regression: peek() must leave the frame available for
    the send loop's later get() — unlike get(), it must not clear the fresh
    flag."""
    slot = LatestFrameSlot()
    slot.put(b"a")
    assert slot.peek() == b"a"
    assert slot.get() == b"a"  # still there after the peek
    assert slot.get() is None  # now genuinely consumed


def test_slot_peek_repeatable():
    slot = LatestFrameSlot()
    slot.put(b"a")
    assert slot.peek() == b"a"
    assert slot.peek() == b"a"
    assert slot.peek() == b"a"


def test_slot_peek_returns_none_when_never_filled():
    slot = LatestFrameSlot()
    assert slot.peek() is None


def test_slot_peek_does_not_touch_any_counter():
    """peek() is an inspection, not a consumption — counting it would
    corrupt duplicates_suppressed (and any other counter)."""
    m = StreamMetrics()
    slot = LatestFrameSlot(metrics=m)
    slot.put(b"a")
    for _ in range(5):
        slot.peek()
    snap = m.snapshot()
    assert snap["duplicates_suppressed"] == 0
    assert snap["frames_dropped"] == 0
    assert snap["frames_encoded"] == 1  # only the put() counted


def test_slot_counts_encoded_frames():
    """The JPEG path used to be invisible in metrics (encoded=0 always,
    even while streaming) because only EncodedFrameQueue.put() counted
    frames_encoded. put() must count every frame the encoder produces,
    same as the H.264 path, regardless of whether it is later read,
    overwritten, or suppressed as a duplicate."""
    m = StreamMetrics()
    slot = LatestFrameSlot(metrics=m)
    slot.put(b"a")
    slot.put(b"b")  # overwrites "a" — still encoded, just also dropped
    assert m.snapshot()["frames_encoded"] == 2
    slot.get()
    slot.get()  # duplicate-suppressed read must not affect frames_encoded
    assert m.snapshot()["frames_encoded"] == 2
