"""Frame handoff between the encoder callback and the socket send loop.

Two strategies, chosen by codec:

* ``EncodedFrameQueue`` — lossless bounded FIFO for inter-frame codecs
  (H.264). Losing one encoded frame corrupts every frame until the next
  keyframe, so a drop is never acceptable in isolation. On overflow the
  queue drains and demands a keyframe resync instead, then rejects
  delta frames — explicitly, and counted — until that keyframe lands,
  because they reference discarded data and cannot decode.
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
        # Set whenever the decoder's reference chain is known to be broken
        # and every delta frame until the next IDR is therefore unsendable:
        # on overflow (the backlog was discarded), or by require_keyframe()
        # when the send loop retransmitted a cached IDR as an idle
        # keepalive. Cleared by the first keyframe that arrives.
        self._needs_keyframe = False

    def _count(self, name: str, n: int = 1) -> None:
        if self._metrics is not None:
            self._metrics.incr(name, n)

    @property
    def needs_keyframe(self) -> bool:
        """True while the stream is waiting for an IDR to resync (after an
        overflow, or after an idle-keepalive IDR retransmission — see
        require_keyframe()). Non-keyframes are rejected by put() while
        this holds."""
        with self._lock:
            return self._needs_keyframe

    def require_keyframe(self) -> None:
        """Arm the keyframe gate from outside an overflow.

        Same state, same guarantee as the overflow path: until an IDR
        arrives, put() rejects delta frames because they reference a
        picture the decoder no longer holds.

        The second caller is the send loop's idle keepalive. When the
        capture source goes quiet (PipeWire only delivers on damage, so a
        virtual display with nothing repainting on it produces no frames
        at all), the loop retransmits the last cached IDR to keep the
        connection alive. That leaves the decoder's reference state at
        "just decoded that IDR" while the encoder's own reference state is
        wherever it actually left off — so the next delta frame the
        encoder emits would decode against the wrong picture. Arming the
        gate here makes that frame unsendable until a fresh IDR resyncs
        both ends, which is exactly the invariant this class already
        enforces after an overflow.
        """
        with self._lock:
            self._needs_keyframe = True

    def put(self, data: bytes, is_keyframe: bool = False) -> bool:
        """Enqueue an encoded frame.

        Returns True when the frame was accepted into the queue. Returns
        False when

        * the queue has already been closed;
        * the queue was full (the backlog is discarded wholesale and a
          resync is requested, because dropping a single inter-frame
          would corrupt the stream silently);
        * the stream is still waiting for the post-overflow keyframe and
          this frame is a delta frame. Such a frame references discarded
          data, so it is provably undecodable — transmitting it would
          produce exactly the visible corruption an overflow resync
          exists to end. The rejection is explicit and counted under
          `frames_dropped`, which is what distinguishes it from the
          silent mid-GOP drops this module was written to eliminate.

        Threading model: exactly one producer thread calls put() (the
        GStreamer appsink `new-sample` callback, a single streaming
        thread) and exactly one consumer thread calls get() (the socket
        send loop). close() is called from a third thread (the GLib main
        context, during pipeline teardown) and genuinely races with
        put() — see close() for how that race is made safe.
        """
        # The closed check, keyframe gate, sequence assignment, and
        # enqueue are kept atomic together under the lock so that close()
        # can flip `_closed` and be guaranteed no put() started afterward
        # will enqueue a frame (see close()). This lock is NOT here to
        # guard against concurrent producers — there is only ever one
        # producer thread in this module's threading model.
        with self._lock:
            if self._closed:
                return False
            if self._needs_keyframe and not is_keyframe:
                self._count("frames_dropped")
                return False
            frame = Frame(data=data, seq=self._seq, is_keyframe=is_keyframe)
            self._seq += 1
            try:
                self._q.put_nowait(frame)
                overflowed = False
            except queue.Full:
                overflowed = True
            else:
                # Only frames that actually made it into the queue count as
                # encoded — anything discarded below is reported as dropped.
                self._count("frames_encoded")
                if is_keyframe:
                    self._needs_keyframe = False

        if not overflowed:
            return True

        with self._lock:
            self._needs_keyframe = True

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
            # Mirrors EncodedFrameQueue.put(): every frame the encoder
            # actually produces is counted here, so the JPEG path is no
            # longer invisible in the per-second metrics log (it used to
            # report encoded=0 even while frames were streaming).
            self._count("frames_encoded")

    def get(self) -> Optional[bytes]:
        """Return the newest unread frame, or None if nothing new arrived.

        An empty poll increments `duplicates_suppressed`: the send loop asked
        for a frame faster than the encoder produced one, so a byte-identical
        retransmission of the previous JPEG was avoided. That counter is
        therefore a measure of saved bandwidth, NOT a fault — a steady stream
        of them in the per-second log means the send loop is polling faster
        than the capture framerate, which is normal and healthy.
        """
        with self._lock:
            if not self._fresh:
                self._count("duplicates_suppressed")
                return None
            self._fresh = False
            return self._data

    def peek(self) -> Optional[bytes]:
        """Return the current payload without consuming it.

        Unlike get(), this never clears the fresh flag — a get() that
        follows a peek() still returns the same payload, as if the peek()
        had never happened. Returns None if the slot has never been filled.

        This exists for callers that only need to know a frame exists (e.g.
        the handshake's dimension probe) and must not steal it: on an idle
        encoder — a static virtual display that PipeWire only repaints on
        damage — the current frame may be the last one that will ever
        arrive, and get()'s consuming read would leave the slot permanently
        empty for the send loop.

        Deliberately does not touch any metric counter: this is an
        inspection, not a consumption, and counting it would corrupt the
        `duplicates_suppressed` signal (which measures genuinely-empty
        polls, not peeks).
        """
        with self._lock:
            return self._data
