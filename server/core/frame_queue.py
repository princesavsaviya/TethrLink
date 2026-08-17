"""Frame handoff between the encoder callback and the socket send loop.

Two strategies, chosen by codec:

* ``EncodedFrameQueue`` — lossless bounded FIFO for inter-frame codecs
  (H.264). Losing one encoded frame corrupts every frame until the next
  keyframe, so a drop is never acceptable in isolation. On overflow the
  queue drains and demands a keyframe resync instead.
* ``LatestFrameSlot`` — latest-value-wins, correct for intra-only codecs
  (JPEG) where each frame is independent.

No `gi`/GStreamer import here on purpose: this logic is the heart of the
correctness fix and must be testable without hardware.
"""

import queue
import threading
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass(frozen=True)
class Frame:
    data: bytes
    seq: int
    is_keyframe: bool


_SENTINEL = object()


class EncodedFrameQueue:
    def __init__(
        self,
        maxsize: int = 4,
        metrics=None,
        on_overflow: Optional[Callable[[], None]] = None,
    ):
        self._q = queue.Queue(maxsize=maxsize)
        self._metrics = metrics
        self._on_overflow = on_overflow
        self._seq = 0
        self._lock = threading.Lock()
        self._closed = False

    def _count(self, name: str, n: int = 1) -> None:
        if self._metrics is not None:
            self._metrics.incr(name, n)

    def put(self, data: bytes, is_keyframe: bool = False) -> bool:
        """Enqueue an encoded frame.

        Returns True normally. Returns False when the queue was full (the
        backlog is discarded wholesale and a resync is requested, because
        dropping a single inter-frame would corrupt the stream silently)
        or when the queue has already been closed.

        Threading model: exactly one producer thread calls put() (the
        GStreamer appsink `new-sample` callback, a single streaming
        thread) and exactly one consumer thread calls get() (the socket
        send loop). close() is called from a third thread (the GLib main
        context, during pipeline teardown) and genuinely races with
        put() — see close() for how that race is made safe.
        """
        self._count("frames_encoded")
        # The closed check, sequence assignment, and enqueue are kept
        # atomic together under the lock so that close() can flip
        # `_closed` and be guaranteed no put() started afterward will
        # enqueue a frame (see close()). This lock is NOT here to guard
        # against concurrent producers — there is only ever one producer
        # thread in this module's threading model.
        with self._lock:
            if self._closed:
                return False
            frame = Frame(data=data, seq=self._seq, is_keyframe=is_keyframe)
            self._seq += 1
            try:
                self._q.put_nowait(frame)
                overflowed = False
            except queue.Full:
                overflowed = True

        if not overflowed:
            return True

        # Overflow handling (drain, metric increments, on_overflow()) runs
        # outside the lock deliberately: on_overflow() calls back into
        # GStreamer, and holding a lock across that call risks deadlock.
        # This is only safe to leave non-atomic because there is a single
        # producer thread; genuine concurrent producers would double-count
        # queue_overflows/frames_dropped and could invoke on_overflow()
        # more than once for what should be a single overflow event.
        discarded = self.drain()
        self._count("frames_dropped", discarded)
        self._count("queue_overflows")
        if self._on_overflow is not None:
            self._on_overflow()
        return False

    def get(self, timeout: float) -> Optional[Frame]:
        """Pop exactly one frame, or None on timeout/close. Never repeats."""
        try:
            item = self._q.get(timeout=timeout)
        except queue.Empty:
            return None
        if item is _SENTINEL:
            return None
        return item

    def drain(self) -> int:
        removed = 0
        while True:
            try:
                self._q.get_nowait()
                removed += 1
            except queue.Empty:
                return removed

    def close(self) -> None:
        """Release a consumer blocked in get().

        Called from a third thread (the GLib main context) that races
        with the single producer thread's put() calls. Setting `_closed`
        under the same lock that put() checks means that once the
        `with self._lock` block below returns, no put() that starts
        afterward can enqueue another frame — so draining and then
        enqueuing the sentinel cannot lose the race to a producer
        refilling the queue in the gap between the two (which is how the
        sentinel used to get silently dropped on a full queue).
        """
        with self._lock:
            self._closed = True
        self.drain()
        try:
            self._q.put_nowait(_SENTINEL)
        except queue.Full:
            # Should be unreachable: `_closed` guarantees no further put()
            # can succeed, and drain() just emptied the queue immediately
            # before this call on this same thread.
            pass


class LatestFrameSlot:
    def __init__(self, metrics=None):
        self._lock = threading.Lock()
        self._data: Optional[bytes] = None
        self._fresh = False
        self._metrics = metrics

    def _count(self, name: str, n: int = 1) -> None:
        if self._metrics is not None:
            self._metrics.incr(name, n)

    def put(self, data: bytes) -> None:
        with self._lock:
            if self._fresh:
                # An unread frame is being replaced.
                self._count("frames_dropped")
            self._data = data
            self._fresh = True

    def get(self) -> Optional[bytes]:
        with self._lock:
            if not self._fresh:
                self._count("duplicates_suppressed")
                return None
            self._fresh = False
            return self._data
