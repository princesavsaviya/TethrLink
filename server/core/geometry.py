"""Capture geometry resolution.

The virtual display is sized from both ends of the connection: its height
matches the host monitor, so the two screens' shared edge is fully
traversable in GNOME's display arrangement, while its aspect ratio matches
the Android device, so nothing is stretched on the device's panel. See
`resolve_capture_size` for the full rationale and precedence rules.

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

    Precedence:

    1. An explicit user override (`config_w`/`config_h`) always wins.
    2. When both the device and the monitor are known, an "edge-aligned"
       size is derived from the two of them (see below) rather than simply
       matching the device.
    3. Device known, monitor not: use the device's own dimensions.
    4. Neither known: fall back to the PC's primary monitor.

    Matching the device exactly (the old behaviour) turned out to cause
    three problems that hardware testing surfaced:

    - Decode cost: a 2960x1848 stream at 30 fps asks for ~164 Mpx/s, close
      to the practical ceiling for a single H.264 decode on the device.
    - Broken edge traversal: GNOME only lets the mouse cross between two
      monitors where their edges vertically overlap. A virtual display much
      taller than the laptop panel beside it leaves the excess height
      untraversable — the cursor hits an invisible wall.
    - Unreadable UI: a desktop rendered 1:1 at a tablet's native pixel
      density is physically tiny on the panel; fixing that properly needs a
      Mutter scale factor, which is out of scope here.

    Matching the monitor's height fixes all three, but the host is
    typically 16:9 while the device is often 16:10 (or another ratio), and
    the H.264 client path renders into a full-screen Surface with no aspect
    correction — so a monitor-shaped stream stretches visibly on the
    device. The fix is to take the height from the monitor (so the shared
    screen edge is fully traversable) but the *aspect ratio* from the
    device (so nothing is stretched on the panel, and the upscale from
    captured pixels to physical pixels is uniform in both axes rather than
    stretched more in one than the other):

        target_height = min(monitor_h, device_h)
        target_width  = round(target_height * (device_w / device_h))

    For example, a 1920x1080 monitor with a 2960x1848 device yields
    1730x1080: the host's height, widened to the device's 1.60:1 aspect.

    `min(...)` also means we never ask for more pixels than the device can
    actually show — a 4K-tall monitor paired with a 1848-tall device still
    tops out at 1848, since sending more than the panel can display would
    be pure waste.

    The aspect ratio must be computed from the device dimensions *after*
    landscape normalisation, not the raw report: the Android client always
    streams in landscape (see `normalise_orientation`), so a device that
    happened to report portrait at connect time must have its aspect ratio
    read from the landscape shape it will actually stream, or the derived
    size comes out inverted and the stretch this change removes comes back
    worse than before.

    The device's report is only trusted if it is plausible at all; an
    implausible one falls back to the monitor rather than building a
    virtual monitor out of nonsense. Whatever is chosen is normalised to
    landscape at the end too — a no-op for the edge-aligned and device-only
    paths, which already normalised going in, but still needed for the
    monitor-only fallback and to leave an explicit `orientation="portrait"`
    alone.
    """
    if config_w > 0 and config_h > 0:
        width, height = config_w, config_h
    elif is_plausible_size(device_w, device_h):
        # Normalise before deriving anything from the aspect ratio — see the
        # docstring's note on why this ordering matters.
        norm_device_w, norm_device_h = normalise_orientation(
            device_w, device_h, orientation
        )
        if is_plausible_size(monitor_w, monitor_h):
            target_height = min(monitor_h, norm_device_h)
            target_width = round(target_height * (norm_device_w / norm_device_h))
            width, height = target_width, target_height
        else:
            width, height = norm_device_w, norm_device_h
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
