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
    if cfg.rate_control != RateControl.CQP:
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
