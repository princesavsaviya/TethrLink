# Encoder Negotiation and Device Geometry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut end-to-end latency by replacing fixed near-lossless software encoding with a negotiated hardware encoder under real rate control, then make the virtual display match the Android device's dimensions exactly so nothing is ever rescaled.

**Architecture:** A vendor-neutral encoder layer probes candidate GStreamer elements at runtime — verifying not just that an element exists but that its rate-control enum offers the mode we need *on this GPU* and that it reaches PLAYING — then maps a normalized config onto each encoder's native property names. Geometry then drops the fixed 1280 px cap and the `videoscale` step: the capture caps request the client's reported dimensions directly, which is what drives Mutter's virtual monitor size.

**Tech Stack:** Python 3.12, PyGObject (`gi`), GStreamer 1.24, pytest 9, GNOME/Mutter 46 ScreenCast API v4.

## Global Constraints

- Python 3.12; run tests with `./venv/bin/python -m pytest`, NEVER bare `pytest`.
- Never diagnose GStreamer with bare `gst-inspect-1.0` — Anaconda shadows it with a 1.14.1 build reporting zero encoders. Use `/usr/bin/gst-inspect-1.0`.
- `server/core/frame_queue.py`, `metrics.py`, `preflight.py`, and the new `encoder.py` MUST NOT import `gi` except where explicitly stated — the encoder *adapter* logic must be unit-testable without GStreamer.
- Valid metric counter names are exactly: `frames_encoded`, `frames_sent`, `frames_dropped`, `duplicates_suppressed`, `queue_overflows`, `keyframe_requests`. `incr()` raises `KeyError` otherwise.
- Dropping a RAW frame upstream of the encoder is permitted; dropping an ENCODED frame is forbidden. Do not alter the `queue leaky=downstream` / `appsink drop=false` arrangement.
- No protocol or wire-format changes. Released Android clients must keep working unmodified. The client already reports its dimensions in the existing 94-byte handshake.
- B-frames must be explicitly zero on every encoder — they add reordering latency. Defaults differ per encoder.
- The shipped default codec stays JPEG. H.264 is selected via `TETHRLINK_CODEC=h264`.
- Work happens on branch `work/video-quality-review`.
- Preserve the JPEG path's behaviour throughout; it is the shipping default.

## Measured Baseline (this machine, 120-frame `videotestsrc` benchmark)

Encode throughput, so the plan's choices are grounded rather than assumed:

| Encoder | 1280×720 | 1920×1080 | 2960×1848 |
|---|---|---|---|
| `x264enc` medium qp1 (**current**) | 76.4 | 30.5 | **12.8** |
| `x264enc` ultrafast CBR 25 Mbps | 141.2 | 71.2 | 38.8 |
| `x264enc` veryfast CBR 25 Mbps | 84.2 | 47.7 | 29.7 |
| `nvh264enc` CBR 25 Mbps | 319.6 | 195.7 | **92.6** |
| `vaapih264enc` CBR 25 Mbps | runtime ERROR | runtime ERROR | runtime ERROR |
| `vah264lpenc` | `rate-control` offers **only `cqp`** on this Intel GPU | | |

Two facts drive the design:

1. **The current settings cannot sustain the target frame rate at device resolution** (12.8 fps at 2960×1848). Encoder work must land before geometry or latency gets worse, not better.
2. **Element presence proves nothing.** `vah264lpenc` exposes `rate-control`, but its enum type is `GstVaEncoderRateControl_H264_LP_renderD128` — probed from the actual render node — and on this GPU contains only `cqp`. `vaapih264enc` advertises `cbr` and still fails at runtime. Probing must check enum contents and actually run the encoder.

## Verified Environment Facts

- `org.gnome.Mutter.ScreenCast` Version = 4, GNOME Shell 46.0.
- `Session.RecordVirtual(IN a{sv} properties, OUT o stream_path)` — takes **no** size arguments.
- **Virtual monitor size is driven by PipeWire caps negotiation.** Requesting `video/x-raw,width=2960,height=1848` on `pipewiresrc` caused Mutter to create monitor `Meta-0` at exactly 2960×1848 (preferred mode 2960×1848, one mode). Verified empirically.
- Mutter offered scales `[1.0, 1.333, 1.6, 2.0, 2.667]` for that virtual monitor.
- The H.264 pipeline currently has **no caps filter on `pipewiresrc`**; the JPEG pipeline does. That omission is why H.264 gets Mutter's default size and then rescales.
- The test client (Samsung SM-X920) reports 2960×1848. 2960 is 16-aligned (185×16); 1848 is 8-aligned but not 16-aligned, so encoders pad to 1856 and signal cropping in the SPS.

## File Structure

- `server/core/encoder.py` — **new.** Pure-logic encoder selection and property mapping. No `gi` import; GStreamer interaction is injected. This is where the normalized-config → native-property translation lives, and it carries the bulk of the new test coverage.
- `server/core/server_core.py` — modified. `PipeWireCapture.__init__` consumes the chosen encoder spec rather than hardcoding `x264enc`; geometry changes remove `videoscale` and the `h264_width` cap.
- `tests/test_encoder.py` — **new.** Unit tests for selection, enum gating, and property mapping.
- `tests/test_geometry.py` — **new.** Unit tests for dimension resolution and alignment.

---

### Task 1: Encoder capability model

Pure data and logic, no GStreamer. Establishes the vocabulary every later task uses.

**Files:**
- Create: `server/core/encoder.py`
- Test: `tests/test_encoder.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `RateControl` — enum-like constants `CBR = "cbr"`, `VBR = "vbr"`, `CQP = "cqp"`.
  - `EncoderConfig` dataclass: `bitrate_kbps: int`, `gop_length: int`, `rate_control: str`, `low_latency: bool = True`.
  - `EncoderSpec` dataclass: `element: str`, `is_hardware: bool`, `rate_control: str`, `props: dict[str, str]`, plus method `to_pipeline_fragment() -> str` rendering `element k=v k=v …`.
  - `default_bitrate_kbps(width, height, fps, bits_per_pixel=0.1) -> int` — clamped to `[8000, 60000]`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_encoder.py`:

```python
import pytest

from server.core.encoder import (
    EncoderConfig,
    EncoderSpec,
    RateControl,
    default_bitrate_kbps,
)


def test_bitrate_scales_with_pixels_and_rate():
    small = default_bitrate_kbps(1280, 720, 30)
    large = default_bitrate_kbps(2960, 1848, 60)
    assert large > small


def test_bitrate_is_clamped_at_the_bottom():
    assert default_bitrate_kbps(320, 240, 15) == 8000


def test_bitrate_is_clamped_at_the_top():
    assert default_bitrate_kbps(7680, 4320, 120) == 60000


def test_bitrate_uses_the_bits_per_pixel_factor():
    lean = default_bitrate_kbps(1920, 1080, 60, bits_per_pixel=0.05)
    rich = default_bitrate_kbps(1920, 1080, 60, bits_per_pixel=0.2)
    assert rich > lean


def test_pipeline_fragment_renders_element_then_properties():
    spec = EncoderSpec(
        element="x264enc",
        is_hardware=False,
        rate_control=RateControl.CBR,
        props={"bitrate": "25000", "key-int-max": "45"},
    )
    fragment = spec.to_pipeline_fragment()
    assert fragment.startswith("x264enc ")
    assert "bitrate=25000" in fragment
    assert "key-int-max=45" in fragment


def test_pipeline_fragment_with_no_props_is_just_the_element():
    spec = EncoderSpec(
        element="fakeenc", is_hardware=True, rate_control=RateControl.CQP, props={}
    )
    assert spec.to_pipeline_fragment() == "fakeenc"


def test_encoder_config_defaults_to_low_latency():
    cfg = EncoderConfig(bitrate_kbps=25000, gop_length=45, rate_control=RateControl.CBR)
    assert cfg.low_latency is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_encoder.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'server.core.encoder'`

- [ ] **Step 3: Write minimal implementation**

Create `server/core/encoder.py`:

```python
"""Vendor-neutral H.264 encoder selection and property mapping.

Deliberately free of any `gi`/GStreamer import: GStreamer interaction is
injected by the caller, so the selection and property-translation logic —
where the vendor differences actually live — is unit-testable on a machine
with no GStreamer and no GPU.
"""

from dataclasses import dataclass, field
from typing import Dict

MIN_BITRATE_KBPS = 8000
MAX_BITRATE_KBPS = 60000


class RateControl:
    CBR = "cbr"
    VBR = "vbr"
    CQP = "cqp"


@dataclass
class EncoderConfig:
    """What we want, expressed without reference to any encoder's API."""
    bitrate_kbps: int
    gop_length: int
    rate_control: str
    low_latency: bool = True


@dataclass
class EncoderSpec:
    """A concrete, resolved encoder choice ready to be put in a pipeline."""
    element: str
    is_hardware: bool
    rate_control: str
    props: Dict[str, str] = field(default_factory=dict)

    def to_pipeline_fragment(self) -> str:
        if not self.props:
            return self.element
        rendered = " ".join(f"{k}={v}" for k, v in self.props.items())
        return f"{self.element} {rendered}"


def default_bitrate_kbps(
    width: int, height: int, fps: int, bits_per_pixel: float = 0.1
) -> int:
    """Derive a sane bitrate target from the raw pixel rate.

    Replaces the fixed `quantizer=1` the pipeline used to carry, which set
    quality with no regard for how many bytes it produced.
    """
    raw = width * height * fps * bits_per_pixel / 1000.0
    return int(max(MIN_BITRATE_KBPS, min(MAX_BITRATE_KBPS, raw)))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_encoder.py -v`
Expected: PASS, 7 passed

- [ ] **Step 5: Commit**

```bash
git add server/core/encoder.py tests/test_encoder.py
git commit -m "feat: add vendor-neutral encoder capability model"
```

---

### Task 2: Per-encoder property adapters

The heart of hardware-agnostic support. Each encoder names the same concepts differently, and units differ — `openh264enc` takes bits/s where the others take kbit/s.

**Files:**
- Modify: `server/core/encoder.py`
- Test: `tests/test_encoder.py`

**Interfaces:**
- Consumes: `EncoderConfig`, `EncoderSpec`, `RateControl` from Task 1.
- Produces:
  - `CANDIDATES: tuple[str, ...]` — priority order, hardware first, software last.
  - `HARDWARE_ELEMENTS: frozenset[str]`.
  - `build_spec(element: str, config: EncoderConfig, available_rate_controls: set[str]) -> EncoderSpec | None` — returns None when the element cannot satisfy the config at all.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_encoder.py`:

```python
from server.core.encoder import (
    CANDIDATES,
    HARDWARE_ELEMENTS,
    build_spec,
)


def _cbr_config():
    return EncoderConfig(
        bitrate_kbps=25000, gop_length=45, rate_control=RateControl.CBR
    )


def test_candidate_order_puts_hardware_before_software():
    order = list(CANDIDATES)
    assert order.index("nvh264enc") < order.index("x264enc")
    assert order.index("vaapih264enc") < order.index("x264enc")
    assert order[-1] == "openh264enc"


def test_hardware_elements_excludes_software_encoders():
    assert "x264enc" not in HARDWARE_ELEMENTS
    assert "openh264enc" not in HARDWARE_ELEMENTS
    assert "nvh264enc" in HARDWARE_ELEMENTS


def test_x264_maps_to_its_own_property_names():
    spec = build_spec("x264enc", _cbr_config(), {RateControl.CBR})
    assert spec.props["bitrate"] == "25000"
    assert spec.props["key-int-max"] == "45"
    assert spec.props["pass"] == "cbr"
    assert spec.props["bframes"] == "0"
    assert spec.props["tune"] == "zerolatency"
    assert spec.is_hardware is False


def test_x264_low_latency_uses_a_realtime_preset():
    """speed-preset=medium was the old value and is not a realtime preset."""
    spec = build_spec("x264enc", _cbr_config(), {RateControl.CBR})
    assert spec.props["speed-preset"] == "ultrafast"


def test_nvenc_maps_to_gop_size_and_disables_bframes():
    spec = build_spec("nvh264enc", _cbr_config(), {RateControl.CBR})
    assert spec.props["bitrate"] == "25000"
    assert spec.props["gop-size"] == "45"
    assert spec.props["bframes"] == "0"
    assert spec.is_hardware is True


def test_nvenc_uses_its_own_rate_control_property_and_nicknames():
    """NVENC carries rate control on `rc-mode`, not `rate-control`, and has
    no `cqp` nickname at all — it is `constqp`. Verified against a real
    element; guessing either would fail at pipeline-parse time."""
    cbr = build_spec("nvh264enc", _cbr_config(), {RateControl.CBR})
    assert "rate-control" not in cbr.props
    assert cbr.props["rc-mode"] == "cbr-ld-hq"

    cqp_cfg = EncoderConfig(
        bitrate_kbps=25000, gop_length=45, rate_control=RateControl.CQP
    )
    cqp = build_spec("nvh264enc", cqp_cfg, {RateControl.CQP})
    assert cqp.props["rc-mode"] == "constqp"
    assert "bitrate" not in cqp.props


def test_vaapi_uses_keyframe_period_and_max_bframes():
    spec = build_spec("vaapih264enc", _cbr_config(), {RateControl.CBR})
    assert spec.props["keyframe-period"] == "45"
    assert spec.props["max-bframes"] == "0"
    assert spec.props["rate-control"] == "cbr"


def test_va_lowpower_uses_b_frames_with_a_hyphen():
    spec = build_spec("vah264lpenc", _cbr_config(), {RateControl.CBR})
    assert spec.props["b-frames"] == "0"
    assert spec.props["key-int-max"] == "45"


def test_openh264_takes_bits_per_second_not_kilobits():
    """The classic unit footgun: every other encoder here uses kbit/s."""
    spec = build_spec("openh264enc", _cbr_config(), {RateControl.CBR})
    assert spec.props["bitrate"] == "25000000"


def test_degrades_to_cqp_when_cbr_is_unavailable_on_this_gpu():
    """vah264lpenc on Intel Comet Lake offers only cqp — no CBR exists.

    Degrading is deliberately preferred over refusing: an Intel user should
    still get hardware encoding, just without a bitrate target.
    """
    spec = build_spec("vah264lpenc", _cbr_config(), {RateControl.CQP})
    assert spec is not None
    assert spec.rate_control == RateControl.CQP
    assert "bitrate" not in spec.props
    assert spec.props["rate-control"] == "cqp"


def test_returns_none_when_no_rate_control_is_offered_at_all():
    spec = build_spec("vah264lpenc", _cbr_config(), set())
    assert spec is None


def test_honours_cqp_when_that_is_what_was_asked_for():
    cqp_cfg = EncoderConfig(
        bitrate_kbps=25000, gop_length=45, rate_control=RateControl.CQP
    )
    spec = build_spec("vah264lpenc", cqp_cfg, {RateControl.CQP})
    assert spec is not None
    assert spec.rate_control == RateControl.CQP
    assert "bitrate" not in spec.props


def test_unknown_element_yields_none():
    assert build_spec("nosuchenc", _cbr_config(), {RateControl.CBR}) is None


def test_every_candidate_has_an_adapter():
    """A candidate with no adapter would be silently unusable."""
    cfg = _cbr_config()
    for element in CANDIDATES:
        built = build_spec(element, cfg, {RateControl.CBR, RateControl.CQP})
        assert built is not None, f"no adapter for {element}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_encoder.py -v`
Expected: FAIL — `ImportError: cannot import name 'CANDIDATES'`

- [ ] **Step 3: Write minimal implementation**

Append to `server/core/encoder.py`:

```python
# Hardware first, software last. Presence in this list means "worth trying",
# never "known to work" — see `probe.py` callers, which must instantiate.
CANDIDATES = (
    "nvh264enc",       # NVIDIA NVENC
    "vah264enc",       # modern VA, standard
    "vah264lpenc",     # modern VA, low-power
    "vaapih264enc",    # legacy VAAPI
    "qsvh264enc",      # Intel QSV
    "v4l2h264enc",     # ARM / embedded
    "x264enc",         # software
    "openh264enc",     # software
)

HARDWARE_ELEMENTS = frozenset({
    "nvh264enc", "vah264enc", "vah264lpenc",
    "vaapih264enc", "qsvh264enc", "v4l2h264enc",
})

# Which property carries rate control. Verified by introspecting real
# elements: these genuinely differ, and guessing "rate-control" everywhere
# silently fails on the two most important encoders.
RC_PROPERTY = {
    "x264enc":      "pass",
    "nvh264enc":    "rc-mode",
    "vaapih264enc": "rate-control",
    "vah264enc":    "rate-control",
    "vah264lpenc":  "rate-control",
    "qsvh264enc":   "rate-control",
}

# Normalized mode -> the nickname this encoder actually accepts. Every value
# here was confirmed settable on a real element; nvh264enc notably has no
# "cqp" (it is spelled "constqp") and offers "cbr-ld-hq", a low-delay
# high-quality CBR mode that suits a latency-sensitive link better than
# plain "cbr".
RC_NICKS = {
    "x264enc":      {RateControl.CBR: "cbr", RateControl.CQP: "qual"},
    "nvh264enc":    {RateControl.CBR: "cbr-ld-hq", RateControl.VBR: "vbr-hq",
                     RateControl.CQP: "constqp"},
    "vaapih264enc": {RateControl.CBR: "cbr", RateControl.VBR: "vbr",
                     RateControl.CQP: "cqp"},
    "vah264enc":    {RateControl.CBR: "cbr", RateControl.VBR: "vbr",
                     RateControl.CQP: "cqp"},
    "vah264lpenc":  {RateControl.CBR: "cbr", RateControl.VBR: "vbr",
                     RateControl.CQP: "cqp"},
    "qsvh264enc":   {RateControl.CBR: "cbr", RateControl.VBR: "vbr",
                     RateControl.CQP: "cqp"},
}


def _rc_nick(element: str, mode: str) -> str:
    return RC_NICKS.get(element, {}).get(mode, mode)


def _x264(cfg: EncoderConfig) -> Dict[str, str]:
    props = {
        "key-int-max": str(cfg.gop_length),
        "bframes": "0",
    }
    if cfg.low_latency:
        # `medium` was the shipped value and is not a realtime preset; it cost
        # latency for quality nobody could see over a compressed link.
        props["speed-preset"] = "ultrafast"
        props["tune"] = "zerolatency"
    if cfg.rate_control == RateControl.CQP:
        props["pass"] = "qual"
        props["quantizer"] = "21"
    else:
        props["pass"] = "cbr"
        props["bitrate"] = str(cfg.bitrate_kbps)
    return props


def _nvenc(cfg: EncoderConfig) -> Dict[str, str]:
    # NVENC has NO "cqp" nickname — it is spelled "constqp" — and its
    # rate-control lives on `rc-mode`, not `rate-control`. Both were verified
    # by set_property against a real element.
    props = {"gop-size": str(cfg.gop_length), "bframes": "0"}
    if cfg.low_latency:
        props["zerolatency"] = "true"
    props["rc-mode"] = _rc_nick("nvh264enc", cfg.rate_control)
    if cfg.rate_control == RateControl.CQP:
        props["qp-const"] = "21"
    else:
        props["bitrate"] = str(cfg.bitrate_kbps)
    return props


def _vaapi(cfg: EncoderConfig) -> Dict[str, str]:
    props = {"keyframe-period": str(cfg.gop_length), "max-bframes": "0"}
    props["rate-control"] = _rc_nick("vaapih264enc", cfg.rate_control)
    if cfg.rate_control != RateControl.CQP:
        props["bitrate"] = str(cfg.bitrate_kbps)
    return props


def _va(cfg: EncoderConfig) -> Dict[str, str]:
    # The modern `va` plugin spells it `b-frames` and `key-int-max`.
    props = {"key-int-max": str(cfg.gop_length), "b-frames": "0"}
    props["rate-control"] = _rc_nick("vah264lpenc", cfg.rate_control)
    if cfg.rate_control != RateControl.CQP:
        props["bitrate"] = str(cfg.bitrate_kbps)
    return props


def _qsv(cfg: EncoderConfig) -> Dict[str, str]:
    props = {"gop-size": str(cfg.gop_length)}
    if cfg.rate_control != RateControl.CQP:
        props["rate-control"] = _rc_nick("qsvh264enc", cfg.rate_control)
        props["bitrate"] = str(cfg.bitrate_kbps)
    return props


def _v4l2(cfg: EncoderConfig) -> Dict[str, str]:
    # v4l2 encoders take tuning through a controls string rather than
    # individual properties.
    if cfg.rate_control == RateControl.CQP:
        return {}
    return {
        "extra-controls":
            f"controls,video_bitrate={cfg.bitrate_kbps * 1000},"
            f"h264_i_frame_period={cfg.gop_length}",
    }


def _openh264(cfg: EncoderConfig) -> Dict[str, str]:
    props = {"gop-size": str(cfg.gop_length)}
    if cfg.rate_control != RateControl.CQP:
        # openh264enc counts in bits per second, unlike every other encoder
        # here. Getting this wrong yields a 1000x-wrong bitrate.
        props["bitrate"] = str(cfg.bitrate_kbps * 1000)
    return props


_ADAPTERS = {
    "x264enc":      _x264,
    "nvh264enc":    _nvenc,
    "vaapih264enc": _vaapi,
    "vah264enc":    _va,
    "vah264lpenc":  _va,
    "qsvh264enc":   _qsv,
    "v4l2h264enc":  _v4l2,
    "openh264enc":  _openh264,
}


def build_spec(element, config, available_rate_controls):
    """Resolve `config` against one encoder, or None if it cannot comply.

    `available_rate_controls` is what the element's enum actually offers on
    THIS machine. It is a parameter rather than a lookup because the answer is
    hardware-dependent: `vah264lpenc` on Intel Comet Lake exposes only `cqp`,
    and its enum type name is even suffixed with the render node it was
    probed from.
    """
    adapter = _ADAPTERS.get(element)
    if adapter is None:
        return None

    wanted = config.rate_control
    if wanted not in available_rate_controls:
        if RateControl.CQP not in available_rate_controls:
            return None
        # Degrade to constant-quantizer rather than refusing the encoder.
        config = EncoderConfig(
            bitrate_kbps=config.bitrate_kbps,
            gop_length=config.gop_length,
            rate_control=RateControl.CQP,
            low_latency=config.low_latency,
        )

    return EncoderSpec(
        element=element,
        is_hardware=element in HARDWARE_ELEMENTS,
        rate_control=config.rate_control,
        props=adapter(config),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_encoder.py -v`
Expected: PASS, 21 passed

- [ ] **Step 5: Commit**

```bash
git add server/core/encoder.py tests/test_encoder.py
git commit -m "feat: add per-encoder property adapters

Each encoder names the same concepts differently and openh264enc counts
bitrate in bits/s rather than kbit/s. Rate-control availability is passed in
rather than assumed, because the enum contents are probed from the actual
GPU — vah264lpenc offers only cqp on Intel Comet Lake."
```

---

### Task 3: Runtime encoder probe against real GStreamer

This is the only part of encoder selection that touches `gi`. Kept separate so Tasks 1–2 stay testable without it.

**Files:**
- Modify: `server/core/server_core.py`
- Test: manual — requires real GStreamer.

**Interfaces:**
- Consumes: `CANDIDATES`, `build_spec`, `EncoderConfig`, `RateControl`, `default_bitrate_kbps` from Tasks 1–2.
- Produces: `probe_rate_controls(element) -> set[str]` and `select_encoder(config) -> EncoderSpec | None`, both module-level in `server_core.py`. `select_encoder` caches its result in a module-level variable so repeated connections do not re-probe.

- [ ] **Step 1: Add the imports**

With the other `server.core` imports in `server/core/server_core.py`:

```python
from server.core.encoder import (
    CANDIDATES,
    RC_NICKS,
    RC_PROPERTY,
    EncoderConfig,
    EncoderSpec,
    RateControl,
    build_spec,
    default_bitrate_kbps,
)
```

- [ ] **Step 2: Add the probe helpers**

Add immediately above `log_gstreamer_preflight`:

```python
_ENCODER_CACHE = {}


def probe_rate_controls(element: str) -> set:
    """Report which rate-control modes this element accepts ON THIS MACHINE.

    Determined by actually setting the property and seeing what sticks, which
    is more reliable than introspecting the enum class through PyGObject. The
    answer is genuinely hardware-dependent: the enum is populated from the
    render node, so `vah264lpenc` accepts only `cqp` on Intel Comet Lake
    while `vaapih264enc` accepts all three.

    Elements with no rate-control property at all (v4l2h264enc, openh264enc)
    are reported as supporting everything, because their adapters express
    rate control through other properties entirely.
    """
    factory = Gst.ElementFactory.find(element)
    if factory is None:
        return set()
    try:
        el = factory.create(None)
    except Exception:
        return set()
    if el is None:
        return set()

    prop_name = RC_PROPERTY.get(element)
    if prop_name is None:
        return {RateControl.CBR, RateControl.VBR, RateControl.CQP}

    try:
        if el.find_property(prop_name) is None:
            return {RateControl.CBR, RateControl.VBR, RateControl.CQP}
    except Exception:
        return {RateControl.CBR, RateControl.VBR, RateControl.CQP}

    modes = set()
    for mode in (RateControl.CBR, RateControl.VBR, RateControl.CQP):
        nick = RC_NICKS.get(element, {}).get(mode)
        if nick is None:
            continue
        try:
            el.set_property(prop_name, nick)
            modes.add(mode)
        except Exception:
            pass
    return modes


def _encoder_runs(spec: EncoderSpec) -> bool:
    """Actually encode a frame. Presence and properties are not enough.

    vaapih264enc advertises cbr and still fails with an internal data stream
    error at runtime on this hardware, so nothing short of running it counts
    as verification.
    """
    desc = (
        f"videotestsrc num-buffers=2 ! "
        f"video/x-raw,format=NV12,width=320,height=240,framerate=30/1 ! "
        f"{spec.to_pipeline_fragment()} ! fakesink sync=false"
    )
    pipeline = None
    try:
        pipeline = Gst.parse_launch(desc)
        if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            return False
        msg = pipeline.get_bus().timed_pop_filtered(
            5 * Gst.SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR
        )
        return msg is not None and msg.type == Gst.MessageType.EOS
    except Exception as e:
        log.debug("Encoder %s failed verification: %s", spec.element, e)
        return False
    finally:
        if pipeline is not None:
            pipeline.set_state(Gst.State.NULL)


def select_encoder(config: EncoderConfig):
    """Pick the best encoder that actually works, hardware first.

    Result is cached: probing instantiates pipelines, which is too expensive
    to repeat per client connection.
    """
    key = (config.rate_control, config.gop_length, config.bitrate_kbps,
           config.low_latency)
    if key in _ENCODER_CACHE:
        return _ENCODER_CACHE[key]

    chosen = None
    for element in CANDIDATES:
        spec = build_spec(element, config, probe_rate_controls(element))
        if spec is None:
            continue
        if not _encoder_runs(spec):
            log.info("Encoder %s present but not usable — skipping", element)
            continue
        chosen = spec
        break

    if chosen is None:
        log.error("No usable H.264 encoder found")
    else:
        log.info("Encoder selected: %s (%s, rate-control=%s)",
                 chosen.element,
                 "hardware" if chosen.is_hardware else "software",
                 chosen.rate_control)
    _ENCODER_CACHE[key] = chosen
    return chosen
```

- [ ] **Step 3: Verify the probe picks a hardware encoder here**

Run:

```bash
./venv/bin/python -c "
import logging; logging.basicConfig(level=logging.INFO)
from server.core.server_core import select_encoder, probe_rate_controls
from server.core.encoder import EncoderConfig, RateControl, CANDIDATES
for el in CANDIDATES:
    print(f'{el:16s} rate-controls={sorted(probe_rate_controls(el))}')
cfg = EncoderConfig(bitrate_kbps=25000, gop_length=45, rate_control=RateControl.CBR)
spec = select_encoder(cfg)
print('CHOSEN:', spec.to_pipeline_fragment() if spec else None)
" 2>&1 | grep -viE "^\(|critical"
```

Expected: `vah264lpenc` reports `['cqp']` only. `nvh264enc` is chosen with `rc-mode=cbr bitrate=25000 gop-size=45 bframes=0 zerolatency=true`. `vaapih264enc` should be reported as "present but not usable" if it is reached.

- [ ] **Step 4: Confirm the suite still passes**

Run: `./venv/bin/python -m pytest -q`
Expected: all tests pass (85 at this point)

- [ ] **Step 5: Commit**

```bash
git add server/core/server_core.py
git commit -m "feat: probe and verify H.264 encoders at runtime

Reads each element's rate-control enum, which GStreamer populates from the
actual render node, then verifies by encoding real frames — vaapih264enc
advertises cbr yet fails at runtime, so instantiation is the only proof."
```

---

### Task 4: Use the selected encoder in the pipeline

**Files:**
- Modify: `server/core/server_core.py` — `PipeWireCapture.__init__` signature and the H.264 pipeline string; the `PipeWireCapture(...)` construction site in `_handle_client`.

**Interfaces:**
- Consumes: `select_encoder`, `EncoderConfig`, `default_bitrate_kbps`, `RateControl` from Tasks 1–3.
- Produces: `PipeWireCapture.__init__` gains a keyword argument `encoder_spec: EncoderSpec | None = None`; when None the H.264 branch falls back to the previous hardcoded `x264enc` fragment so behaviour never silently disappears.

- [ ] **Step 1: Accept the spec in the constructor**

In `PipeWireCapture.__init__`, add `encoder_spec=None` as the final keyword parameter, and store it as `self._encoder_spec = encoder_spec` before the pipeline string is built.

- [ ] **Step 2: Use it in the H.264 pipeline string**

In the `codec == CODEC_H264` branch, replace the single `x264enc …` line with a fragment chosen from the spec. Build the fragment before `pipeline_str`:

```python
            if self._encoder_spec is not None:
                encoder_fragment = self._encoder_spec.to_pipeline_fragment()
            else:
                # Preserved fallback: the previously hardcoded software encoder.
                encoder_fragment = (
                    "x264enc tune=zerolatency speed-preset=ultrafast "
                    "pass=cbr bitrate=25000 key-int-max=45 bframes=0"
                )
```

Then in `pipeline_str` replace the encoder line with:

```python
                f"! {encoder_fragment} "
```

Keep the `option-string="colorprim=bt709:transfer=bt709:colormatrix=bt709:fullrange=off"` **only** when the chosen element is `x264enc`, since that property is x264-specific and would fail to parse on other encoders. Append it to the fragment conditionally:

```python
            if encoder_fragment.startswith("x264enc"):
                encoder_fragment += (
                    ' option-string="colorprim=bt709:transfer=bt709:'
                    'colormatrix=bt709:fullrange=off"'
                )
```

- [ ] **Step 3: Select the encoder at stream start**

At the `PipeWireCapture(` construction site in `_handle_client`, before constructing it, choose the encoder for the H.264 case and pass it:

```python
                enc_spec = None
                if codec == CODEC_H264:
                    enc_spec = select_encoder(EncoderConfig(
                        bitrate_kbps=(
                            self._config.bitrate
                            if self._config.bitrate > 0
                            else default_bitrate_kbps(width, height, self._live_fps)
                        ),
                        gop_length=self._live_fps,
                        rate_control=RateControl.CBR,
                    ))
```

then add `encoder_spec=enc_spec,` to the `PipeWireCapture(...)` call.

Note this finally gives the `bitrate` config field an effect — it was previously parsed and never referenced.

- [ ] **Step 4: Confirm the module imports and the suite passes**

Run: `./venv/bin/python -m pytest -q && ./venv/bin/python -c "import server.core.server_core; print('import ok')"`
Expected: all tests pass, then `import ok`

- [ ] **Step 5: Commit**

```bash
git add server/core/server_core.py
git commit -m "feat: encode with the negotiated encoder under rate control

Replaces the hardcoded x264enc quantizer=1 with the probed encoder and a
real bitrate target, and finally wires up the bitrate config field, which
until now was parsed and never referenced."
```

---

### Task 5: Resolve capture geometry from the client's dimensions

Pure logic, no GStreamer, so alignment rules are properly testable.

**Files:**
- Create: `server/core/geometry.py`
- Test: `tests/test_geometry.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `align_for_encoder(width, height, alignment=2) -> tuple[int, int]` — rounds **up** to the alignment.
  - `resolve_capture_size(device_w, device_h, config_w, config_h, monitor_w, monitor_h, max_w=0, max_h=0) -> tuple[int, int]` — precedence: explicit config → device dimensions → primary monitor; then clamped to `max_w`/`max_h` when non-zero, preserving aspect; then aligned.

- [ ] **Step 1: Write the failing test**

Create `tests/test_geometry.py`:

```python
from server.core.geometry import align_for_encoder, resolve_capture_size


def test_alignment_leaves_already_aligned_sizes_alone():
    assert align_for_encoder(1920, 1080, 2) == (1920, 1080)


def test_alignment_rounds_odd_dimensions_up():
    """H.264 4:2:0 chroma requires even dimensions."""
    assert align_for_encoder(1079, 723, 2) == (1080, 724)


def test_alignment_to_sixteen_rounds_up():
    """1848 is 8-aligned but not 16-aligned; encoders pad and crop via SPS."""
    assert align_for_encoder(2960, 1848, 16) == (2960, 1856)


def test_device_dimensions_win_over_the_monitor():
    """The whole point: match the tablet, not the PC's screen."""
    assert resolve_capture_size(
        device_w=2960, device_h=1848,
        config_w=0, config_h=0,
        monitor_w=1920, monitor_h=1080,
    ) == (2960, 1848)


def test_explicit_config_overrides_the_device():
    assert resolve_capture_size(
        device_w=2960, device_h=1848,
        config_w=1600, config_h=900,
        monitor_w=1920, monitor_h=1080,
    ) == (1600, 900)


def test_falls_back_to_monitor_when_device_is_unknown():
    assert resolve_capture_size(
        device_w=0, device_h=0,
        config_w=0, config_h=0,
        monitor_w=1920, monitor_h=1080,
    ) == (1920, 1080)


def test_clamps_to_decoder_limit_preserving_aspect():
    w, h = resolve_capture_size(
        device_w=2960, device_h=1848,
        config_w=0, config_h=0,
        monitor_w=1920, monitor_h=1080,
        max_w=1920, max_h=1920,
    )
    assert w <= 1920 and h <= 1920
    # 2960/1848 = 1.6017; allow a pixel of rounding either way
    assert abs((w / h) - (2960 / 1848)) < 0.01


def test_clamp_is_ignored_when_limits_are_zero():
    assert resolve_capture_size(
        device_w=2960, device_h=1848,
        config_w=0, config_h=0,
        monitor_w=1920, monitor_h=1080,
        max_w=0, max_h=0,
    ) == (2960, 1848)


def test_result_is_always_even():
    w, h = resolve_capture_size(
        device_w=1079, device_h=723,
        config_w=0, config_h=0,
        monitor_w=1920, monitor_h=1080,
    )
    assert w % 2 == 0 and h % 2 == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_geometry.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'server.core.geometry'`

- [ ] **Step 3: Write minimal implementation**

Create `server/core/geometry.py`:

```python
"""Capture geometry resolution.

The virtual display should be the shape of the Android device, so that
nothing in the path ever rescales: capture, encode, decode and render all
agree, which is both the sharpest and the cheapest arrangement.

No `gi` import — the alignment and precedence rules are worth testing on
their own.
"""

from typing import Tuple


def align_for_encoder(width: int, height: int, alignment: int = 2) -> Tuple[int, int]:
    """Round up to what the encoder can accept.

    H.264 4:2:0 chroma needs even dimensions; encoders prefer multiples of 16
    and pad internally, signalling the real size as SPS cropping.
    """
    def up(value: int) -> int:
        remainder = value % alignment
        return value if remainder == 0 else value + (alignment - remainder)

    return (up(width), up(height))


def resolve_capture_size(
    device_w: int, device_h: int,
    config_w: int, config_h: int,
    monitor_w: int, monitor_h: int,
    max_w: int = 0, max_h: int = 0,
) -> Tuple[int, int]:
    """Decide what size to capture at.

    Precedence: an explicit user override, else the connected device's own
    dimensions, else the PC's primary monitor. Device dimensions winning over
    the monitor is the entire point — previously the device's reported size
    was stored and then ignored, so a 2960x1848 tablet received a
    PC-shaped 1920x1080 desktop scaled down to 1280 wide and stretched back
    up on the client.
    """
    if config_w > 0 and config_h > 0:
        width, height = config_w, config_h
    elif device_w > 0 and device_h > 0:
        width, height = device_w, device_h
    else:
        width, height = monitor_w, monitor_h

    # Respect a decoder's maximum, keeping the device's aspect ratio: a tall
    # portrait mode can exceed a MediaCodec limit that the same pixel count
    # in landscape would not.
    if max_w > 0 and max_h > 0 and (width > max_w or height > max_h):
        scale = min(max_w / width, max_h / height)
        width = int(width * scale)
        height = int(height * scale)

    return align_for_encoder(width, height, 2)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_geometry.py -v`
Expected: PASS, 9 passed

- [ ] **Step 5: Commit**

```bash
git add server/core/geometry.py tests/test_geometry.py
git commit -m "feat: resolve capture geometry from the connected device"
```

---

### Task 6: Capture at device resolution and stop rescaling

**Files:**
- Modify: `server/core/server_core.py` — `_handle_client` resolution block, and `PipeWireCapture.__init__`'s H.264 pipeline string.

**Interfaces:**
- Consumes: `resolve_capture_size` from Task 5.
- Produces: no new public interface. `h264_width` becomes unused by the H.264 path.

- [ ] **Step 1: Add the import**

With the other `server.core` imports:

```python
from server.core.geometry import resolve_capture_size
```

- [ ] **Step 2: Resolve resolution from the device**

In `_handle_client`, replace the resolution block that currently prefers the primary monitor. The existing code looks like:

```python
        if self._config.width > 0 and self._config.height > 0:
            width, height = self._config.width, self._config.height
            self._log(f"Using configured resolution: {width}×{height}")
        else:
            width, height = resolve_resolution(self._bus, 0, 0)
            self._log(f"Using primary monitor resolution: {width}×{height}")
```

Replace with:

```python
        mon_w, mon_h = resolve_resolution(self._bus, 0, 0)
        width, height = resolve_capture_size(
            device_w=screen_w, device_h=screen_h,
            config_w=self._config.width, config_h=self._config.height,
            monitor_w=mon_w, monitor_h=mon_h,
        )
        if self._config.width > 0 and self._config.height > 0:
            self._log(f"Using configured resolution: {width}×{height}")
        elif screen_w > 0 and screen_h > 0:
            self._log(f"Matching device resolution: {width}×{height}")
        else:
            self._log(f"Using primary monitor resolution: {width}×{height}")
```

Note the comment above the old `device_width`/`device_height` assignment says the device dims are "used by UI canvas, not for resolution". That is no longer true — update it to say the dimensions now drive the capture size.

- [ ] **Step 3: Request the size on pipewiresrc and delete the rescale**

In the `codec == CODEC_H264` pipeline string, remove the `aspect`/`h264_h` computation and the `videoscale` element, and put a caps filter directly on `pipewiresrc` output. Empirically verified: this is what drives Mutter's virtual monitor size — requesting 2960×1848 produced a virtual monitor at exactly that size.

Replace the H.264 branch's size handling so the pipeline reads:

```python
            pipeline_str = (
                f"pipewiresrc path={node_id} always-copy=true "
                # This caps filter is what determines the virtual monitor's
                # size — Mutter's RecordVirtual takes no size arguments, the
                # size comes from PipeWire format negotiation. The JPEG branch
                # always had this; the H.264 branch did not, which is why it
                # got Mutter's default and then rescaled.
                f"! video/x-raw,width={width},height={height} "
                # Dropping RAW frames is safe — it only lowers framerate.
                f"! queue leaky=downstream max-size-buffers=2 max-size-bytes=0 max-size-time=0 "
                f"! videorate "
                f"! videoconvert "
                f"! video/x-raw,format=NV12,framerate={fps}/1,colorimetry=bt709 "
                f"! {encoder_fragment} "
                f"! h264parse config-interval=-1 "
                f"! video/x-h264,stream-format=byte-stream,alignment=au,profile=high "
                f"! appsink name=sink emit-signals=true "
                f"  max-buffers={APPSINK_MAX_BUFFERS} drop=false sync=false"
            )
```

`videoscale` is gone: capture, encode and render now all use one size, so there is no resampling anywhere in the path.

- [ ] **Step 4: Confirm import and suite**

Run: `./venv/bin/python -m pytest -q && ./venv/bin/python -c "import server.core.server_core; print('import ok')"`
Expected: all tests pass, then `import ok`

- [ ] **Step 5: Commit**

```bash
git add server/core/server_core.py
git commit -m "feat: capture at the device's resolution with no rescaling

The device's reported dimensions now drive the virtual monitor size via the
pipewiresrc caps filter, which is what Mutter negotiates against. Removes
videoscale and the fixed 1280px cap, so capture, encode, decode and render
all agree on one size and nothing is resampled. Also removes the source of
the client-side aspect stretch, since the H.264 surface path renders to a
full-screen SurfaceView with no aspect correction of its own."
```

---

### Task 7: Hardware verification

**Files:**
- Modify: `docs/superpowers/plans/2026-08-17-verification-log.md`

- [ ] **Step 1: Confirm the unit suite**

Run: `./venv/bin/python -m pytest -q`
Expected: all pass, roughly 94 tests.

- [ ] **Step 2: Confirm encoder selection**

Start the server and record the `Encoder selected:` line. On this machine expect `nvh264enc (hardware, rate-control=cbr)`. Also record any "present but not usable" lines — `vaapih264enc` is expected to appear there.

```bash
cd /home/prince/TethrLink && TETHRLINK_CODEC=h264 python3 -m server.app.main
```

- [ ] **Step 3: Confirm geometry**

Connect the device and check the log says `Matching device resolution: 2960×1848` (or the client's own size). Confirm the logged GStreamer pipeline contains `video/x-raw,width=2960,height=1848` on `pipewiresrc`, contains **no** `videoscale`, and that `Streaming →` reports those dimensions.

**Acceptance:** the image fills the tablet screen with correct proportions — no vertical stretch — and text is sharp rather than soft, because nothing is being upscaled.

- [ ] **Step 4: Confirm latency and stability**

Run 10 minutes of real use. Record the metrics line.

**Acceptance:** `dropped` and `overflows` stay at 0 after the startup transient, sustained fps is at or near the configured rate, and interaction feels responsive rather than laggy.

- [ ] **Step 5: Confirm no JPEG regression**

Run without `TETHRLINK_CODEC` for 2 minutes. JPEG must behave exactly as before: no reconnect storm, `sent` climbing, `dup_suppressed` non-zero.

- [ ] **Step 6: Record results and commit**

Append the observed values to the verification log, then:

```bash
git add docs/superpowers/plans/2026-08-17-verification-log.md
git commit -m "docs: record encoder and geometry verification results"
```

---

## Out of Scope

- **Logical-size (DPI) scaling.** Mutter offers scales `[1.0, 1.333, 1.6, 2.0, 2.667]` for the virtual monitor, but applying one needs `DisplayConfig.ApplyMonitorsConfig`, which rewrites the user's display layout and must be reverted on disconnect. It also needs the device's DPI, which the current handshake does not carry. Separate plan.
- **Handshake extension** for DPI, refresh rate and decoder capability limits. `resolve_capture_size` already accepts `max_w`/`max_h` so the clamp is ready when the client can report them.
- **Client-side aspect correction.** Task 6 removes the mismatch at the source, so this becomes defensive only — needed for new clients meeting old servers. Requires an APK rebuild.
- H.265/AV1, reverse control channel, live rotation renegotiation, `.deb` packaging fixes.
