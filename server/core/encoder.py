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


# Hardware first, software last. Presence in this list means "worth trying",
# never "known to work" — see the callers in `server_core.py`
# (`select_encoder`/`_encoder_runs`), which must instantiate and actually run
# the element at the real capture size before believing it.
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


# The quantizer used whenever an encoder runs in CQP mode. CQP has no bitrate
# target by construction, so without an explicit QP the encoder runs at
# whatever its own default is — which is the unbounded-quality behaviour that
# replacing the old `quantizer=1` was meant to eliminate. 21 is a visually
# transparent, sanely-sized default for desktop content.
CQP_QUANTIZER = "21"

# Which property carries that quantizer. Like RC_PROPERTY, these genuinely
# differ per element and a single guessed name would silently do nothing on
# most of them. Verified on this machine by set_property against real
# elements: `x264enc.quantizer`, `nvh264enc.qp-const`, `vaapih264enc.init-qp`,
# `vah264lpenc.qpi`/`.qpp` all accept 21 with rate-control set to their CQP
# nickname.
#
# UNVERIFIED: `qsvh264enc` is absent on this machine, so `qp-i`/`qp-p` are
# taken from the GStreamer qsv plugin's documented property names and have not
# been confirmed against a real element. If they are wrong the element will
# fail to parse, and `_encoder_runs()` in server_core.py — which instantiates
# and actually runs each candidate before selecting it — rejects it rather
# than shipping a broken pipeline. `vah264enc` is also absent here, but it
# shares the `va` plugin (and this adapter) with the verified `vah264lpenc`.
#
# Tuples, not single names: the `va` and `qsv` plugins carry a separate
# quantizer per frame type. B-frames are explicitly disabled by every adapter,
# so only the I and P quantizers are set.
CQP_PROPERTY = {
    "x264enc":      ("quantizer",),
    "nvh264enc":    ("qp-const",),
    "vaapih264enc": ("init-qp",),
    "vah264enc":    ("qpi", "qpp"),
    "vah264lpenc":  ("qpi", "qpp"),
    "qsvh264enc":   ("qp-i", "qp-p"),
}


def _rc_nick(element: str, mode: str) -> str:
    return RC_NICKS.get(element, {}).get(mode, mode)


def _apply_cqp(props: Dict[str, str], element: str) -> None:
    """Set this element's constant-quantizer properties in place."""
    for name in CQP_PROPERTY.get(element, ()):
        props[name] = CQP_QUANTIZER


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
        _apply_cqp(props, "x264enc")
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
        _apply_cqp(props, "nvh264enc")
    else:
        props["bitrate"] = str(cfg.bitrate_kbps)
    return props


def _vaapi(cfg: EncoderConfig) -> Dict[str, str]:
    props = {"keyframe-period": str(cfg.gop_length), "max-bframes": "0"}
    props["rate-control"] = _rc_nick("vaapih264enc", cfg.rate_control)
    if cfg.rate_control == RateControl.CQP:
        # CQP has no bitrate target, so without this the encoder runs at its
        # own default QP — no quality target and no size ceiling. `_x264` and
        # `_nvenc` always set one; this used to not, which is the whole reason
        # the degrade-to-CQP path was producing unbounded output.
        _apply_cqp(props, "vaapih264enc")
    else:
        props["bitrate"] = str(cfg.bitrate_kbps)
    return props


def _va(cfg: EncoderConfig) -> Dict[str, str]:
    # The modern `va` plugin spells it `b-frames` and `key-int-max`.
    props = {"key-int-max": str(cfg.gop_length), "b-frames": "0"}
    props["rate-control"] = _rc_nick("vah264lpenc", cfg.rate_control)
    if cfg.rate_control == RateControl.CQP:
        # This is the documented `vah264lpenc`-on-Intel case: it offers only
        # cqp, so every Intel user reaching this adapter lands here. Setting
        # the quantizer explicitly is what gives them a quality target at all.
        _apply_cqp(props, "vah264lpenc")
    else:
        props["bitrate"] = str(cfg.bitrate_kbps)
    return props


def _qsv(cfg: EncoderConfig) -> Dict[str, str]:
    props = {"gop-size": str(cfg.gop_length)}
    # qsvh264enc is absent on this machine, so this is unverified: the
    # GStreamer qsv plugin documents a `b-frames` property. If it turns out
    # not to exist, the runtime verification step (instantiating and
    # running each encoder before selecting it) will reject this encoder
    # rather than silently leaving B-frames enabled.
    props["b-frames"] = "0"
    # Always set rate-control, the same as `_vaapi`/`_va` — only `bitrate`
    # is skipped in CQP mode. This used to live inside the `!= CQP` guard,
    # so CQP mode emitted no rate-control property at all and silently
    # inherited whatever qsvh264enc defaults to.
    props["rate-control"] = _rc_nick("qsvh264enc", cfg.rate_control)
    if cfg.rate_control == RateControl.CQP:
        # Property names UNVERIFIED — qsvh264enc is absent on this machine, so
        # `qp-i`/`qp-p` come from the plugin's documentation, not from
        # introspection. See CQP_PROPERTY's comment: `_encoder_runs()` is the
        # safety net if they are wrong.
        _apply_cqp(props, "qsvh264enc")
    else:
        props["bitrate"] = str(cfg.bitrate_kbps)
    return props


def _v4l2(cfg: EncoderConfig) -> Dict[str, str]:
    # v4l2 encoders take tuning through a controls string rather than
    # individual properties. v4l2h264enc is also absent on this machine,
    # and which controls a given device exposes is device-specific, so
    # B-frame control is not portably expressible here — guessing a control
    # name risks the element erroring outright on an unknown control. The
    # runtime verification step (instantiating and running each encoder
    # before selecting it) is the safety net for whatever this device
    # actually supports.
    if cfg.rate_control == RateControl.CQP:
        return {}
    return {
        "extra-controls":
            f"controls,video_bitrate={cfg.bitrate_kbps * 1000},"
            f"h264_i_frame_period={cfg.gop_length}",
    }


def _openh264(cfg: EncoderConfig) -> Dict[str, str]:
    # openh264enc exposes no B-frame control property at all — enumerating
    # its properties via PyGObject shows no `bframes`, `b-frames`, or
    # equivalent. Omitting it here is deliberate: do not "fix" this by
    # adding a property, it would fail to parse against the real element.
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
