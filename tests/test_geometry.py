from server.core.geometry import (
    align_for_encoder,
    is_plausible_size,
    normalise_orientation,
    resolve_capture_size,
)


def test_alignment_leaves_already_aligned_sizes_alone():
    assert align_for_encoder(1920, 1080, 2) == (1920, 1080)


def test_alignment_rounds_odd_dimensions_up():
    """H.264 4:2:0 chroma requires even dimensions."""
    assert align_for_encoder(1079, 723, 2) == (1080, 724)


def test_alignment_to_sixteen_rounds_up():
    """1848 is 8-aligned but not 16-aligned; encoders pad and crop via SPS."""
    assert align_for_encoder(2960, 1848, 16) == (2960, 1856)


def test_edge_aligned_size_uses_monitor_height_and_device_aspect():
    """The real case this change is for: 1920x1080 monitor, 2960x1848 tablet.

    Height comes from the monitor (so the shared GNOME edge is fully
    traversable); width is derived from the device's aspect ratio (so
    nothing is stretched on the tablet's panel).
    """
    assert resolve_capture_size(
        device_w=2960, device_h=1848,
        config_w=0, config_h=0,
        monitor_w=1920, monitor_h=1080,
    ) == (1730, 1080)


def test_edge_aligned_size_preserves_the_device_aspect_ratio():
    w, h = resolve_capture_size(
        device_w=2960, device_h=1848,
        config_w=0, config_h=0,
        monitor_w=1920, monitor_h=1080,
    )
    assert abs((w / h) - (2960 / 1848)) < 0.01


def test_edge_aligned_size_never_exceeds_the_devices_own_height():
    """A 4K-tall monitor still tops out at what the tablet can show."""
    assert resolve_capture_size(
        device_w=2960, device_h=1848,
        config_w=0, config_h=0,
        monitor_w=3840, monitor_h=2160,
    ) == (2960, 1848)


def test_edge_aligned_size_when_device_is_shorter_than_the_monitor():
    assert resolve_capture_size(
        device_w=1280, device_h=800,
        config_w=0, config_h=0,
        monitor_w=1920, monitor_h=1080,
    ) == (1280, 800)


def test_device_only_fallback_when_monitor_is_unknown():
    """Device known, monitor implausible: use the device dimensions as-is."""
    assert resolve_capture_size(
        device_w=2960, device_h=1848,
        config_w=0, config_h=0,
        monitor_w=0, monitor_h=0,
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
    # The edge-aligned base size here is 1730x1080; pick a limit small
    # enough that the clamp actually has to act on it.
    w, h = resolve_capture_size(
        device_w=2960, device_h=1848,
        config_w=0, config_h=0,
        monitor_w=1920, monitor_h=1080,
        max_w=1000, max_h=1000,
    )
    assert w <= 1000 and h <= 1000
    # 1730/1080 = 1.6019; allow a pixel of rounding either way
    assert abs((w / h) - (1730 / 1080)) < 0.01


def test_clamp_is_ignored_when_limits_are_zero():
    assert resolve_capture_size(
        device_w=2960, device_h=1848,
        config_w=0, config_h=0,
        monitor_w=1920, monitor_h=1080,
        max_w=0, max_h=0,
    ) == (1730, 1080)


def test_result_is_always_even():
    w, h = resolve_capture_size(
        device_w=1079, device_h=723,
        config_w=0, config_h=0,
        monitor_w=1920, monitor_h=1080,
    )
    assert w % 2 == 0 and h % 2 == 0


# ── Orientation normalisation ────────────────────────────────────────────────
# The client reads its window bounds BEFORE lockToLandscape() runs, so a user
# who connects holding the tablet upright reports a portrait size while the
# streaming surface it will render into is landscape. The H.264 client path
# applies no aspect correction, so a portrait virtual monitor is rendered
# stretched. Released clients cannot be updated; this must be normalised here.


def test_portrait_report_is_swapped_to_landscape_before_deriving_aspect():
    """A tablet connected in portrait reports 1848x2960.

    This is the ordering trap: the aspect ratio used for the edge-aligned
    derivation must come from the normalised *landscape* shape (2960x1848),
    not the raw portrait report — otherwise the aspect comes out inverted
    and the result is a stretched, not fixed, stream. With a 1920x1080
    monitor this must land on the same 1730x1080 as an honestly-reported
    landscape device.
    """
    w, h = resolve_capture_size(
        device_w=1848, device_h=2960,
        config_w=0, config_h=0,
        monitor_w=1920, monitor_h=1080,
    )
    assert (w, h) == (1730, 1080)
    assert w > h, "result must be landscape despite the portrait report"


def test_landscape_report_is_left_alone():
    assert resolve_capture_size(
        device_w=2960, device_h=1848,
        config_w=0, config_h=0,
        monitor_w=1920, monitor_h=1080,
    ) == (1730, 1080)


def test_explicit_portrait_orientation_is_respected():
    """`orientation="portrait"` is a deliberate user choice, not an accident.

    The edge-aligned derivation still applies — height is capped by the
    monitor and width follows the device's (portrait) aspect ratio — but
    the result stays portrait-shaped rather than being flipped to
    landscape.
    """
    w, h = resolve_capture_size(
        device_w=1848, device_h=2960,
        config_w=0, config_h=0,
        monitor_w=1920, monitor_h=1080,
        orientation="portrait",
    )
    assert (w, h) == (674, 1080)
    assert h > w, "explicit portrait must not be flipped to landscape"


def test_normalise_orientation_swaps_only_portrait():
    assert normalise_orientation(1848, 2960) == (2960, 1848)
    assert normalise_orientation(2960, 1848) == (2960, 1848)
    # Square is not portrait — nothing to normalise.
    assert normalise_orientation(1000, 1000) == (1000, 1000)


def test_normalise_orientation_leaves_explicit_portrait_alone():
    assert normalise_orientation(1848, 2960, "portrait") == (1848, 2960)
    assert normalise_orientation(2960, 1848, "portrait") == (2960, 1848)


def test_portrait_swap_happens_before_the_decoder_clamp():
    """The clamp must see the shape actually being encoded, not the report."""
    w, h = resolve_capture_size(
        device_w=1848, device_h=2960,
        config_w=0, config_h=0,
        monitor_w=1920, monitor_h=1080,
        max_w=1920, max_h=1920,
    )
    assert w > h, "clamped result must still be landscape"
    assert w <= 1920 and h <= 1920


# ── Sanity guard on client-reported dimensions ───────────────────────────────


def test_negative_device_dimensions_fall_back_to_the_monitor():
    assert resolve_capture_size(
        device_w=-1920, device_h=-1080,
        config_w=0, config_h=0,
        monitor_w=1920, monitor_h=1080,
    ) == (1920, 1080)


def test_absurdly_large_device_dimensions_fall_back_to_the_monitor():
    assert resolve_capture_size(
        device_w=999999, device_h=999999,
        config_w=0, config_h=0,
        monitor_w=1920, monitor_h=1080,
    ) == (1920, 1080)


def test_tiny_device_dimensions_fall_back_to_the_monitor():
    assert resolve_capture_size(
        device_w=8, device_h=4,
        config_w=0, config_h=0,
        monitor_w=1920, monitor_h=1080,
    ) == (1920, 1080)


def test_one_bad_axis_is_enough_to_reject_the_report():
    assert resolve_capture_size(
        device_w=2960, device_h=0,
        config_w=0, config_h=0,
        monitor_w=1920, monitor_h=1080,
    ) == (1920, 1080)


def test_is_plausible_size_accepts_real_panels():
    assert is_plausible_size(2960, 1848)
    assert is_plausible_size(1848, 2960)
    assert is_plausible_size(1920, 1080)


def test_is_plausible_size_rejects_nonsense():
    assert not is_plausible_size(0, 0)
    assert not is_plausible_size(-1, -1)
    assert not is_plausible_size(100000, 100000)
    assert not is_plausible_size(16, 16)


def test_explicit_config_is_not_rejected_by_the_device_sanity_guard():
    """The guard is for the untrusted wire report, not the local config."""
    assert resolve_capture_size(
        device_w=0, device_h=0,
        config_w=1600, config_h=900,
        monitor_w=1920, monitor_h=1080,
    ) == (1600, 900)
