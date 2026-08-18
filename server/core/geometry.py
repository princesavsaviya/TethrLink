"""Capture geometry resolution.

The virtual display should be the shape of the Android device, so that
nothing in the path ever rescales: capture, encode, decode and render all
agree, which is both the sharpest and the cheapest arrangement.

No `gi` import — the alignment, orientation and precedence rules are worth
testing on their own.
"""

import logging
from typing import Tuple

log = logging.getLogger("TethrLink")

# A client-reported dimension outside this range is not a real display. These
# numbers arrive over the wire in the HELLO handshake, so they are untrusted
# input: a malformed, truncated or hostile report must not be turned into a
# virtual monitor. 16384 is the largest dimension H.264 can signal at all, and
# no panel comes close; below 64 there is nothing worth streaming.
MIN_PLAUSIBLE_DIMENSION = 64
MAX_PLAUSIBLE_DIMENSION = 16384


def is_plausible_size(width: int, height: int) -> bool:
    """Whether a reported size could be a real display.

    Rejects zero, negative and absurd values. Used to decide whether the
    client's report is usable at all, or whether to fall back to the PC's
    primary monitor.
    """
    return (
        MIN_PLAUSIBLE_DIMENSION <= width <= MAX_PLAUSIBLE_DIMENSION
        and MIN_PLAUSIBLE_DIMENSION <= height <= MAX_PLAUSIBLE_DIMENSION
    )


def align_for_encoder(width: int, height: int, alignment: int = 2) -> Tuple[int, int]:
    """Round up to what the encoder can accept.

    H.264 4:2:0 chroma needs even dimensions; encoders prefer multiples of 16
    and pad internally, signalling the real size as SPS cropping.
    """
    def up(value: int) -> int:
        remainder = value % alignment
        return value if remainder == 0 else value + (alignment - remainder)

    return (up(width), up(height))


def normalise_orientation(
    width: int, height: int, orientation: str = "landscape"
) -> Tuple[int, int]:
    """Force landscape geometry unless portrait was explicitly configured.

    The Android client calls `lockToLandscape()` unconditionally as soon as
    streaming starts, so its streaming surface is ALWAYS landscape. But it
    reports `currentWindowMetrics.bounds` at connect time, *before* that lock —
    and the manifest declares `screenOrientation="fullUser"`. A user who
    connects holding the tablet upright therefore reports a portrait size
    (1848x2960), and split-screen or freeform windowing reports window bounds
    rather than display bounds, with the same effect.

    Trusting that report would build a portrait virtual monitor and encode
    portrait video into a landscape surface. The H.264 client path renders
    straight to a MediaCodec Surface with no aspect correction whatsoever
    (unlike JPEG, which goes through an aspect-preserving Canvas), so the
    result is a badly stretched desktop — the exact defect this work removed,
    reintroduced in the other axis. Released clients cannot be updated, so the
    normalisation has to happen here.

    `orientation == "portrait"` is a deliberate user choice and is left alone;
    the swap exists only to normalise an *accidentally* portrait report.
    """
    if orientation == "portrait":
        return width, height
    if height > width:
        log.info(
            "Reported geometry %dx%d is portrait but the client's streaming "
            "surface is always landscape — capturing %dx%d instead",
            width, height, height, width,
        )
        return height, width
    return width, height


def resolve_capture_size(
    device_w: int, device_h: int,
    config_w: int, config_h: int,
    monitor_w: int, monitor_h: int,
    max_w: int = 0, max_h: int = 0,
    orientation: str = "landscape",
) -> Tuple[int, int]:
    """Decide what size to capture at.

    Precedence: an explicit user override, else the connected device's own
    dimensions, else the PC's primary monitor. Device dimensions winning over
    the monitor is the entire point — previously the device's reported size
    was stored and then ignored, so a 2960x1848 tablet received a
    PC-shaped 1920x1080 desktop scaled down to 1280 wide and stretched back
    up on the client.

    The device's report is only trusted if it is plausible at all; an
    implausible one falls back to the monitor rather than building a virtual
    monitor out of nonsense. The result is then normalised to landscape,
    because that is the only shape the client ever renders into — see
    `normalise_orientation`.
    """
    if config_w > 0 and config_h > 0:
        width, height = config_w, config_h
    elif is_plausible_size(device_w, device_h):
        width, height = device_w, device_h
    else:
        # Zero is the ordinary "this client reported nothing" case and is not
        # worth a warning; anything else is a real report we are refusing.
        if device_w != 0 or device_h != 0:
            log.warning(
                "Ignoring implausible device dimensions %dx%d — falling back "
                "to the primary monitor (%dx%d)",
                device_w, device_h, monitor_w, monitor_h,
            )
        width, height = monitor_w, monitor_h

    width, height = normalise_orientation(width, height, orientation)

    # Respect a decoder's maximum, keeping the device's aspect ratio: a tall
    # portrait mode can exceed a MediaCodec limit that the same pixel count
    # in landscape would not. Applied after the orientation normalisation, so
    # the limit is checked against the shape actually being encoded.
    if max_w > 0 and max_h > 0 and (width > max_w or height > max_h):
        scale = min(max_w / width, max_h / height)
        width = int(width * scale)
        height = int(height * scale)

    return align_for_encoder(width, height, 2)
