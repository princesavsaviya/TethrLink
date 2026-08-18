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
