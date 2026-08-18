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
