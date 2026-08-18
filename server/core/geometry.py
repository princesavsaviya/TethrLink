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
