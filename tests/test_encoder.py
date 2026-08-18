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
