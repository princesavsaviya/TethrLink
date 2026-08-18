"""Stream counters. Deliberately free of any `gi`/GStreamer import so this
module is importable and testable on machines without GStreamer."""

import threading

_COUNTERS = (
    "frames_encoded",
    "frames_sent",
    "frames_dropped",
    "duplicates_suppressed",
    "queue_overflows",
    "keyframe_requests",
)


class StreamMetrics:
    def __init__(self):
        self._lock = threading.Lock()
        self._values = {name: 0 for name in _COUNTERS}

    def incr(self, name: str, n: int = 1) -> None:
        with self._lock:
            if name not in self._values:
                raise KeyError(f"unknown counter: {name}")
            self._values[name] += n

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._values)

    def reset(self) -> None:
        with self._lock:
            for name in self._values:
                self._values[name] = 0
