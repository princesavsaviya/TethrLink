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
