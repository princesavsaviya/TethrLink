import threading

import pytest

from server.core.metrics import StreamMetrics


def test_starts_at_zero():
    m = StreamMetrics()
    assert m.snapshot() == {
        "frames_encoded": 0,
        "frames_sent": 0,
        "frames_dropped": 0,
        "duplicates_suppressed": 0,
        "queue_overflows": 0,
        "keyframe_requests": 0,
    }


def test_incr_accumulates():
    m = StreamMetrics()
    m.incr("frames_sent")
    m.incr("frames_sent", 4)
    assert m.snapshot()["frames_sent"] == 5


def test_incr_rejects_unknown_counter():
    m = StreamMetrics()
    with pytest.raises(KeyError):
        m.incr("nonsense")


def test_reset_zeroes_all():
    m = StreamMetrics()
    m.incr("frames_encoded", 3)
    m.reset()
    assert m.snapshot()["frames_encoded"] == 0


def test_concurrent_incr_loses_no_counts():
    m = StreamMetrics()

    def bump():
        for _ in range(1000):
            m.incr("frames_encoded")

    threads = [threading.Thread(target=bump) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert m.snapshot()["frames_encoded"] == 8000
