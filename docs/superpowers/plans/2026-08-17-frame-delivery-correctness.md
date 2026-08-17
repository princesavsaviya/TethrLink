# Frame Delivery Correctness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate H.264 image corruption by replacing the single-slot frame buffer with a lossless bounded FIFO, and add the instrumentation needed to prove it.

**Architecture:** The encoded-frame path becomes lossless: a bounded FIFO between the GStreamer appsink and the socket, with dropping moved *upstream* of the encoder where it is harmless. The send loop's independent `time.sleep` clock is removed so the encoder is the single rate authority. Queue overflow triggers a controlled keyframe resync instead of silent corruption. The FIFO and metrics are pure-Python modules with no GStreamer import, so they are unit-testable without hardware.

**Tech Stack:** Python 3.12, PyGObject (`gi`), GStreamer 1.24, pytest 9.

## Global Constraints

- Python 3.12; server runs from `./venv` (`--system-site-packages`, `gi` from `/usr/lib/python3/dist-packages`).
- Run tests with `./venv/bin/python -m pytest`, never bare `pytest`.
- `server/core/frame_queue.py` and `server/core/metrics.py` MUST NOT import `gi` — they must be importable on a machine with no GStreamer.
- Never use bare `gst-inspect-1.0`; Anaconda shadows it with a crippled 1.14.1 build. Use `/usr/bin/gst-inspect-1.0`.
- Dropping an **encoded** frame is forbidden. Dropping a **raw** frame is permitted.
- JPEG keeps latest-value-wins semantics; only H.264 uses the FIFO.
- Do not change `quantizer`, `key-int-max`, `h264_width`, or resolution handling in this plan — those belong to later phases. One variable at a time.
- Work happens on branch `work/video-quality-review`.

---

### Task 1: Test harness and metrics module

**Files:**
- Create: `pytest.ini`
- Create: `server/core/metrics.py`
- Test: `tests/test_metrics.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `StreamMetrics` class with thread-safe counters `frames_encoded`, `frames_sent`, `frames_dropped`, `duplicates_suppressed`, `queue_overflows`, `keyframe_requests`; methods `incr(name, n=1)`, `snapshot() -> dict[str, int]`, `reset()`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_metrics.py`:

```python
import threading

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
    try:
        m.incr("nonsense")
    except KeyError:
        return
    raise AssertionError("expected KeyError for unknown counter")


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
```

- [ ] **Step 2: Create pytest config so `server` is importable**

Create `pytest.ini`:

```ini
[pytest]
pythonpath = .
testpaths = tests
```

- [ ] **Step 3: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_metrics.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'server.core.metrics'`

- [ ] **Step 4: Write minimal implementation**

Create `server/core/metrics.py`:

```python
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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_metrics.py -v`
Expected: PASS, 5 passed

- [ ] **Step 6: Commit**

```bash
git add pytest.ini server/core/metrics.py tests/test_metrics.py
git commit -m "feat: add thread-safe stream metrics counters"
```

---

### Task 2: Lossless FIFO and latest-value slot

This is the core defect fix. Both classes live in one module because they are two
implementations of the same concept (frame handoff) chosen by codec.

**Files:**
- Create: `server/core/frame_queue.py`
- Test: `tests/test_frame_queue.py`

**Interfaces:**
- Consumes: `StreamMetrics` from Task 1 (optional constructor argument).
- Produces:
  - `Frame` dataclass: fields `data: bytes`, `seq: int`, `is_keyframe: bool`.
  - `EncodedFrameQueue(maxsize=4, metrics=None, on_overflow=None)` with
    `put(data: bytes, is_keyframe: bool = False) -> bool` (False signals overflow),
    `get(timeout: float) -> Frame | None`, `drain() -> int`, `close() -> None`.
  - `LatestFrameSlot(metrics=None)` with `put(data: bytes) -> None` and
    `get() -> bytes | None` (None when nothing new since the last `get`).

- [ ] **Step 1: Write the failing test**

Create `tests/test_frame_queue.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_frame_queue.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'server.core.frame_queue'`

- [ ] **Step 3: Write minimal implementation**

Create `server/core/frame_queue.py`:

```python
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

    def _count(self, name: str, n: int = 1) -> None:
        if self._metrics is not None:
            self._metrics.incr(name, n)

    def put(self, data: bytes, is_keyframe: bool = False) -> bool:
        """Enqueue an encoded frame.

        Returns True normally. Returns False when the queue was full: the
        backlog is discarded wholesale and a resync is requested, because
        dropping a single inter-frame would corrupt the stream silently.
        """
        self._count("frames_encoded")
        with self._lock:
            seq = self._seq
            self._seq += 1
        frame = Frame(data=data, seq=seq, is_keyframe=is_keyframe)
        try:
            self._q.put_nowait(frame)
            return True
        except queue.Full:
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
        """Release a consumer blocked in get()."""
        try:
            self._q.put_nowait(_SENTINEL)
        except queue.Full:
            self.drain()
            try:
                self._q.put_nowait(_SENTINEL)
            except queue.Full:
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_frame_queue.py -v`
Expected: PASS, 18 passed

- [ ] **Step 5: Commit**

```bash
git add server/core/frame_queue.py tests/test_frame_queue.py
git commit -m "feat: add lossless encoded-frame FIFO and latest-value slot

EncodedFrameQueue never returns a duplicate access unit and never drops a
single inter-frame in isolation; on overflow it drains and requests a
keyframe resync. LatestFrameSlot preserves the correct latest-wins
behaviour for JPEG while suppressing retransmission of identical frames."
```

---

### Task 3: Startup preflight diagnostics

**Files:**
- Create: `server/core/preflight.py`
- Test: `tests/test_preflight.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `H264_ENCODER_CANDIDATES` tuple; `probe_encoders(finder, candidates=None) -> list[str]`; `format_preflight(gst_version, plugin_path, available) -> str`.

`probe_encoders` takes the factory-finder as an argument so tests need no GStreamer.

- [ ] **Step 1: Write the failing test**

Create `tests/test_preflight.py`:

```python
from server.core.preflight import (
    H264_ENCODER_CANDIDATES,
    format_preflight,
    probe_encoders,
)


def test_candidates_prefer_hardware_before_software():
    """Software encoders are the last resort, never probed first."""
    order = list(H264_ENCODER_CANDIDATES)
    assert order.index("nvh264enc") < order.index("x264enc")
    assert order.index("vaapih264enc") < order.index("x264enc")
    assert order[-1] == "openh264enc"


def test_probe_returns_only_present_elements_in_priority_order():
    present = {"x264enc", "nvh264enc"}
    found = probe_encoders(finder=lambda name: name in present or None)
    assert found == ["nvh264enc", "x264enc"]


def test_probe_returns_empty_when_nothing_available():
    assert probe_encoders(finder=lambda name: None) == []


def test_probe_accepts_explicit_candidate_list():
    found = probe_encoders(finder=lambda name: True, candidates=("a", "b"))
    assert found == ["a", "b"]


def test_format_preflight_reports_version_and_encoders():
    text = format_preflight("1.24.2", "/usr/lib/gstreamer-1.0", ["nvh264enc"])
    assert "1.24.2" in text
    assert "/usr/lib/gstreamer-1.0" in text
    assert "nvh264enc" in text


def test_format_preflight_warns_when_no_encoder_found():
    text = format_preflight("1.24.2", "/usr/lib/gstreamer-1.0", [])
    assert "WARNING" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_preflight.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'server.core.preflight'`

- [ ] **Step 3: Write minimal implementation**

Create `server/core/preflight.py`:

```python
"""Startup diagnostics.

Reports the GStreamer the *running process* actually loaded. This matters
because a shadowing toolchain on PATH (Anaconda ships GStreamer 1.14.1 with
no encoders registered) makes `gst-inspect-1.0` describe a different
installation than the one the app uses. Never diagnose from the CLI.
"""

from typing import Callable, Iterable, List, Optional

# Hardware first, software last. Element presence does not prove the encoder
# works; real instantiation checks arrive with the encoder-negotiation phase.
H264_ENCODER_CANDIDATES = (
    "nvh264enc",       # NVIDIA NVENC
    "vah264enc",       # modern VA (Intel/AMD)
    "vah264lpenc",     # modern VA, low-power variant
    "vaapih264enc",    # legacy VAAPI
    "qsvh264enc",      # Intel QSV
    "v4l2h264enc",     # ARM / embedded
    "x264enc",         # software
    "openh264enc",     # software
)


def probe_encoders(
    finder: Callable[[str], object],
    candidates: Optional[Iterable[str]] = None,
) -> List[str]:
    """Return available element names, preserving priority order."""
    names = tuple(candidates) if candidates is not None else H264_ENCODER_CANDIDATES
    return [name for name in names if finder(name)]


def format_preflight(
    gst_version: str, plugin_path: str, available: List[str]
) -> str:
    lines = [
        "TethrLink preflight",
        f"  GStreamer version : {gst_version}",
        f"  Plugin path       : {plugin_path}",
    ]
    if available:
        lines.append(f"  H.264 encoders    : {', '.join(available)}")
    else:
        lines.append(
            "  H.264 encoders    : none found — "
            "WARNING: H.264 streaming will not work. "
            "Install gstreamer1.0-plugins-ugly (x264enc)."
        )
    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_preflight.py -v`
Expected: PASS, 6 passed

- [ ] **Step 5: Commit**

```bash
git add server/core/preflight.py tests/test_preflight.py
git commit -m "feat: add startup preflight for GStreamer and encoder discovery"
```

---

### Task 4: Log preflight at server startup

**Files:**
- Modify: `server/core/server_core.py` (imports near the top; `ServerCore.__init__`)
- Test: manual — this is a logging side effect on real GStreamer.

**Interfaces:**
- Consumes: `probe_encoders`, `format_preflight`, `H264_ENCODER_CANDIDATES` from Task 3.
- Produces: `log_gstreamer_preflight()` module-level function in `server_core.py`.

- [ ] **Step 1: Add the import**

In `server/core/server_core.py`, alongside the other `server.core` imports, add:

```python
from server.core.preflight import (
    H264_ENCODER_CANDIDATES,
    format_preflight,
    probe_encoders,
)
```

- [ ] **Step 2: Add the preflight function**

Add this immediately above the `PipeWireCapture` class definition:

```python
def log_gstreamer_preflight() -> None:
    """Log the GStreamer the running process actually loaded."""
    Gst.init(None)
    registry = Gst.Registry.get()
    plugin = registry.find_plugin("coreelements")
    plugin_path = plugin.get_filename() if plugin else "unknown"
    available = probe_encoders(
        finder=Gst.ElementFactory.find,
        candidates=H264_ENCODER_CANDIDATES,
    )
    for line in format_preflight(
        Gst.version_string(), plugin_path, available
    ).splitlines():
        log.info(line)
```

- [ ] **Step 3: Call it once at server construction**

In `ServerCore.__init__`, as the final statement of the method, add:

```python
        log_gstreamer_preflight()
```

- [ ] **Step 4: Verify it runs and reports the real GStreamer**

Run:

```bash
./venv/bin/python -c "
import logging; logging.basicConfig(level=logging.INFO)
from server.core.server_core import log_gstreamer_preflight
log_gstreamer_preflight()
"
```

Expected output contains `GStreamer version : 1.24.2` and an `H.264 encoders` line listing at least `nvh264enc`, `vaapih264enc`, `vah264lpenc`, `x264enc`. It must NOT report 1.14.1 — that would mean conda's build was loaded.

- [ ] **Step 5: Commit**

```bash
git add server/core/server_core.py
git commit -m "feat: log GStreamer preflight at server startup"
```

---

### Task 5: Route encoder output through the FIFO

**Files:**
- Modify: `server/core/server_core.py` — `PipeWireCapture.__init__` (pipeline strings and buffer setup), `PipeWireCapture._on_sample`, `PipeWireCapture.get_frame`

**Interfaces:**
- Consumes: `EncodedFrameQueue`, `LatestFrameSlot` from Task 2; `StreamMetrics` from Task 1.
- Produces: on `PipeWireCapture` — attribute `metrics: StreamMetrics`, attribute `is_inter_frame: bool`, method `get_encoded_frame(timeout: float) -> Frame | None`, and an unchanged `get_frame()` signature for the JPEG path.

- [ ] **Step 1: Add the imports**

In `server/core/server_core.py`, with the other `server.core` imports:

```python
from server.core.frame_queue import EncodedFrameQueue, LatestFrameSlot
from server.core.metrics import StreamMetrics
```

- [ ] **Step 2: Replace the single-slot buffer in `PipeWireCapture.__init__`**

Find these lines near the start of `__init__`:

```python
        self._frame = None
        self._fw    = width
        self._fh    = height
        self._lock  = threading.Lock()
```

Replace with:

```python
        self._fw    = width
        self._fh    = height
        self._lock  = threading.Lock()

        # H.264 is an inter-frame codec: losing one encoded frame corrupts
        # every frame until the next keyframe, so its path must be lossless.
        # JPEG frames are independent, so latest-wins remains correct there.
        self.metrics        = StreamMetrics()
        self.is_inter_frame = (codec == CODEC_H264)
        if self.is_inter_frame:
            self._sink_buffer = EncodedFrameQueue(
                maxsize=4,
                metrics=self.metrics,
                on_overflow=self._request_keyframe,
            )
        else:
            self._sink_buffer = LatestFrameSlot(metrics=self.metrics)
```

- [ ] **Step 3: Add a leaky queue upstream of the encoder and stop dropping downstream**

In the `codec == CODEC_H264` branch, replace the pipeline string with:

```python
            pipeline_str = (
                f"pipewiresrc path={node_id} always-copy=true "
                # Dropping RAW frames is safe — it only lowers framerate.
                # This is the one place in the pipeline where dropping is allowed.
                f"! queue leaky=downstream max-size-buffers=2 max-size-bytes=0 max-size-time=0 "
                f"! videorate "
                f"! videoconvert ! videoscale "
                f"! video/x-raw,format=NV12,width={h264_width},height={h264_h},framerate={fps}/1,colorimetry=bt709 "
                f"! x264enc tune=zerolatency speed-preset=medium pass=qual quantizer=1 key-int-max=45 "
                f"  option-string=\"colorprim=bt709:transfer=bt709:colormatrix=bt709:fullrange=off\" "
                f"! h264parse config-interval=-1 "
                f"! video/x-h264,stream-format=byte-stream,alignment=au,profile=high "
                # drop=false: everything downstream of the encoder is lossless.
                f"! appsink name=sink emit-signals=true "
                f"  max-buffers={APPSINK_MAX_BUFFERS} drop=false sync=false"
            )
```

Leave the JPEG branch exactly as it is — `drop=true` is correct for independent frames.

- [ ] **Step 4: Add the keyframe-request helper**

Add this method to `PipeWireCapture`, immediately before `_on_sample`:

```python
    def _request_keyframe(self) -> None:
        """Force an IDR after a queue overflow.

        The backlog was discarded, so the decoder's reference chain is broken;
        only a keyframe restores it. Sent upstream from the appsink so it
        reaches whichever encoder the pipeline is using.
        """
        try:
            sink = self._pipeline.get_by_name("sink")
            if sink is None:
                return
            pad = sink.get_static_pad("sink")
            if pad is None:
                return
            structure = Gst.Structure.new_from_string(
                "GstForceKeyUnit, all-headers=(boolean)true"
            )
            pad.send_event(Gst.Event.new_custom(
                Gst.EventType.CUSTOM_UPSTREAM, structure
            ))
            self.metrics.incr("keyframe_requests")
            log.warning("Frame queue overflow — forced keyframe resync")
        except Exception as e:
            log.debug("Force-keyframe request failed: %s", e)
```

- [ ] **Step 5: Push samples into the buffer instead of overwriting a slot**

Replace the body of `_on_sample` with:

```python
    def _on_sample(self, sink) -> Gst.FlowReturn:
        sample = sink.emit("pull-sample")
        if not sample:
            return Gst.FlowReturn.ERROR
        buf    = sample.get_buffer()
        ok, mi = buf.map(Gst.MapFlags.READ)
        if ok:
            data = bytes(mi.data)
            buf.unmap(mi)
            if self.is_inter_frame:
                is_key = not buf.has_flags(Gst.BufferFlags.DELTA_UNIT)
                try:
                    caps = sample.get_caps().get_structure(0)
                    with self._lock:
                        self._fw = caps.get_value("width")
                        self._fh = caps.get_value("height")
                except Exception:
                    pass
                self._sink_buffer.put(data, is_keyframe=is_key)
            else:
                self._sink_buffer.put(data)
        return Gst.FlowReturn.OK
```

- [ ] **Step 6: Provide both accessors**

Replace `get_frame` with:

```python
    def get_frame(self):
        """JPEG path: latest frame wins. None when nothing new has arrived."""
        data = self._sink_buffer.get()
        if data is None:
            return None
        with self._lock:
            return (data, self._fw, self._fh)

    def get_encoded_frame(self, timeout: float):
        """H.264 path: exactly one access unit, in order, never repeated."""
        frame = self._sink_buffer.get(timeout)
        if frame is None:
            return None
        with self._lock:
            return (frame.data, self._fw, self._fh)
```

- [ ] **Step 7: Release blocked consumers when the pipeline shuts down**

`_release_pipeline` is the single choke point reached by both the normal
`close()` path and the error path (`_release_and_notify`), so unblocking here
guarantees the send loop wakes on a pipeline error too — not just on a clean
shutdown. Replace the method with:

```python
    def _release_pipeline(self):
        """Set pipeline to NULL to free the PipeWire node."""
        if not self._closed:
            self._closed = True
            self._pipeline.set_state(Gst.State.NULL)
            if self.is_inter_frame:
                # Wake any consumer blocked in get(), or shutdown hangs
                # until the timeout expires.
                self._sink_buffer.close()
```

- [ ] **Step 8: Give the X11 capture path the same attributes**

The send loop in Task 6 reads `capture.is_inter_frame` and `capture.metrics` on
whatever capture object is active. `X11MssCapture` is the other implementation
and always produces JPEG (the mss path forces `CODEC_JPEG`), so it needs both
attributes or the H.264 branch will raise `AttributeError` on X11 sessions.

In `X11MssCapture.__init__`, immediately after `self._frame: Optional[bytes] = None`, add:

```python
        # The mss path always produces JPEG, so latest-wins is correct here.
        self.metrics        = StreamMetrics()
        self.is_inter_frame = False
```

- [ ] **Step 9: Verify existing unit tests still pass and the module imports**

Run: `./venv/bin/python -m pytest -v && ./venv/bin/python -c "import server.core.server_core; print('import ok')"`
Expected: all tests PASS, then `import ok`

- [ ] **Step 10: Commit**

```bash
git add server/core/server_core.py
git commit -m "fix: route encoded frames through lossless FIFO

Replaces the single mutable frame slot, which both dropped frames (when
the encoder outran the send loop) and re-sent identical access units (when
the send loop outran the encoder) — corrupting the H.264 reference chain
in both directions. Dropping now happens only upstream of the encoder via
a leaky queue, where discarding a raw frame merely lowers framerate."
```

---

### Task 6: Drive the send loop from the queue, not a second clock

**Files:**
- Modify: `server/core/server_core.py` — the streaming `while` loop inside `_handle_client` (currently around lines 1043-1059)

**Interfaces:**
- Consumes: `get_encoded_frame(timeout)` and `get_frame()` from Task 5.
- Produces: no new public interface.

- [ ] **Step 1: Replace the streaming loop**

Find the loop that begins `while not self._shutdown.is_set():` in `_handle_client` and replace the whole loop body with:

```python
            while not self._shutdown.is_set():
                if capture.is_inter_frame:
                    # The encoder's framerate cap is the single rate authority.
                    # Blocking here means one clock instead of two; the old
                    # sleep-pacing raced the encoder and corrupted the stream.
                    r = capture.get_encoded_frame(timeout=1.0)
                    if r:
                        raw, _, _ = r
                        conn.sendall(struct.pack(">I", len(raw)) + raw)
                        capture.metrics.incr("frames_sent")
                        frame_count += 1
                else:
                    start = time.monotonic()
                    r = capture.get_frame()
                    if r:
                        raw, _, _ = r
                        conn.sendall(struct.pack(">I", len(raw)) + raw)
                        capture.metrics.incr("frames_sent")
                        frame_count += 1
                    elapsed = time.monotonic() - start
                    sleep   = (1.0 / self._live_fps) - elapsed
                    if sleep > 0:
                        time.sleep(sleep)

                if time.monotonic() >= fps_deadline:
                    snap = capture.metrics.snapshot()
                    self._state.update(fps=frame_count)
                    log.info(
                        "fps=%d encoded=%d sent=%d dropped=%d dup_suppressed=%d "
                        "overflows=%d keyframe_reqs=%d",
                        frame_count,
                        snap["frames_encoded"], snap["frames_sent"],
                        snap["frames_dropped"], snap["duplicates_suppressed"],
                        snap["queue_overflows"], snap["keyframe_requests"],
                    )
                    frame_count  = 0
                    fps_deadline = time.monotonic() + 1.0
```

- [ ] **Step 2: Verify the module still imports and tests pass**

Run: `./venv/bin/python -m pytest -v && ./venv/bin/python -c "import server.core.server_core; print('import ok')"`
Expected: all tests PASS, then `import ok`

- [ ] **Step 3: Commit**

```bash
git add server/core/server_core.py
git commit -m "fix: drive H.264 send loop from the frame queue

Removes the independent sleep-pacing clock on the H.264 path. The send
loop now blocks on the FIFO, making the encoder the single rate authority
and eliminating the drift that caused frames to be dropped or duplicated.
Per-second metrics are logged for verification."
```

---

### Task 7: End-to-end verification

**Files:**
- Create: `docs/superpowers/plans/2026-08-17-verification-log.md`

**Interfaces:**
- Consumes: everything above.
- Produces: a recorded result for the phase acceptance criteria.

- [ ] **Step 1: Confirm the whole unit suite is green**

Run: `./venv/bin/python -m pytest -v`
Expected: 29 passed (5 metrics + 18 frame_queue + 6 preflight)

- [ ] **Step 2: Start the server and capture the preflight line**

Launch the server as normal and record the `TethrLink preflight` lines from the log. Confirm `GStreamer version : 1.24.2` — anything reporting 1.14.1 means conda's build was loaded and the run is invalid.

- [ ] **Step 3: Run a 10-minute H.264 session**

Select the **H.264** codec (Wayland session required), connect the Android device over USB-C, and run for 10 minutes with normal desktop activity — drag windows, scroll text, play a video.

Watch the per-second metrics line. Record the final values.

**Acceptance criteria:**
- `dropped` stays at **0** on the H.264 path
- `overflows` stays at **0** under normal desktop use
- No blocky, smeared colour patches that persist and then snap back — the error-propagation artifact should be gone entirely
- `sent` tracks `encoded` (a small lag of a few frames is the in-flight queue and is expected)

- [ ] **Step 4: Deliberately provoke an overflow**

Stress the machine (e.g. a CPU-saturating build) while streaming. Confirm that when `overflows` increments, `keyframe_reqs` increments with it, and the picture recovers within roughly one frame rather than degrading into persistent corruption.

- [ ] **Step 5: Confirm no JPEG regression**

Switch to the JPEG codec and stream for 2 minutes. Confirm the image is normal and `dup_suppressed` is non-zero — that counter proves identical frames are no longer being retransmitted.

- [ ] **Step 6: Record results**

Write the observed numbers for steps 3–5 into `docs/superpowers/plans/2026-08-17-verification-log.md`, then commit:

```bash
git add docs/superpowers/plans/2026-08-17-verification-log.md
git commit -m "docs: record frame delivery verification results"
```

---

## Out of Scope

Deliberately excluded so the FIFO fix can be attributed in isolation — each has its own later plan:

- Geometry / device-dimension matching, DPI scaling, removing the 1280 px cap (Phase 2)
- Hardware encoder negotiation, rate control, wiring up the dead `bitrate` setting (Phase 3)
- Reverse control channel, client-initiated keyframe requests, GOP tuning (Phase 4)
- Live rotation renegotiation (Phase 4, spec §11)
- `.deb` dependency corrections (Phase 5)
