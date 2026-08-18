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


def test_x264_cqp_mode_uses_qual_pass_and_a_quantizer():
    cqp_cfg = EncoderConfig(
        bitrate_kbps=25000, gop_length=45, rate_control=RateControl.CQP
    )
    spec = build_spec("x264enc", cqp_cfg, {RateControl.CQP})
    assert spec.props["pass"] == "qual"
    assert spec.props["quantizer"] == "21"
    assert "bitrate" not in spec.props


def test_vaapi_cqp_mode_sets_rate_control_cqp_and_skips_bitrate():
    cqp_cfg = EncoderConfig(
        bitrate_kbps=25000, gop_length=45, rate_control=RateControl.CQP
    )
    spec = build_spec("vaapih264enc", cqp_cfg, {RateControl.CQP})
    assert spec.props["rate-control"] == "cqp"
    assert "bitrate" not in spec.props


def test_va_standard_element_is_exercised_independently():
    """vah264enc shares the `_va` adapter with vah264lpenc but, before this
    test, was only ever touched by the generic every-candidate smoke test."""
    spec = build_spec("vah264enc", _cbr_config(), {RateControl.CBR})
    assert spec.props["key-int-max"] == "45"
    assert spec.props["b-frames"] == "0"
    assert spec.props["rate-control"] == "cbr"
    assert spec.props["bitrate"] == "25000"
    assert spec.is_hardware is True


def test_qsv_always_sets_rate_control_and_disables_bframes():
    """Regression test: `_qsv` used to set `rate-control` and `bitrate`
    inside the same `!= CQP` guard, so CQP mode emitted no rate-control
    property at all and silently inherited whatever qsvh264enc defaults to.
    It must set `rate-control` in both modes, like `_vaapi`/`_va` do, and
    disable B-frames explicitly since defaults differ per encoder."""
    cbr = build_spec("qsvh264enc", _cbr_config(), {RateControl.CBR})
    assert cbr.props["rate-control"] == "cbr"
    assert cbr.props["bitrate"] == "25000"
    assert cbr.props["b-frames"] == "0"

    cqp_cfg = EncoderConfig(
        bitrate_kbps=25000, gop_length=45, rate_control=RateControl.CQP
    )
    cqp = build_spec("qsvh264enc", cqp_cfg, {RateControl.CQP})
    assert cqp.props["rate-control"] == "cqp"
    assert "bitrate" not in cqp.props
    assert cqp.props["b-frames"] == "0"


def test_v4l2_extra_controls_carries_bitrate_in_bps_and_iframe_period():
    spec = build_spec("v4l2h264enc", _cbr_config(), {RateControl.CBR})
    controls = spec.props["extra-controls"]
    assert "video_bitrate=25000000" in controls
    assert "h264_i_frame_period=45" in controls


def test_v4l2_cqp_mode_yields_no_controls():
    cqp_cfg = EncoderConfig(
        bitrate_kbps=25000, gop_length=45, rate_control=RateControl.CQP
    )
    spec = build_spec("v4l2h264enc", cqp_cfg, {RateControl.CQP})
    assert spec.props == {}


# ── CQP quality target ───────────────────────────────────────────────────────
# CQP has no bitrate target by construction. An encoder given `rate-control=cqp`
# and no quantizer runs at its own default QP — no quality target and no size
# ceiling, which is the unbounded-quality behaviour that replacing the old
# `quantizer=1` was meant to eliminate. Every adapter that can express a
# quantizer must set one.

from server.core.encoder import CQP_PROPERTY, CQP_QUANTIZER


def _cqp_config():
    return EncoderConfig(
        bitrate_kbps=25000, gop_length=45, rate_control=RateControl.CQP
    )


def test_vaapi_cqp_sets_its_own_quantizer_property():
    """vaapih264enc carries the constant quantizer on `init-qp` — verified by
    set_property against the real element on this machine."""
    spec = build_spec("vaapih264enc", _cqp_config(), {RateControl.CQP})
    assert spec.props["init-qp"] == CQP_QUANTIZER
    assert "bitrate" not in spec.props


def test_va_cqp_sets_its_own_quantizer_properties():
    """The `va` plugin spells them `qpi`/`qpp` — verified by set_property
    against the real vah264lpenc on this machine. This is the documented
    vah264lpenc-on-Intel case, where cqp is the ONLY mode offered."""
    for element in ("vah264enc", "vah264lpenc"):
        spec = build_spec(element, _cqp_config(), {RateControl.CQP})
        assert spec.props["qpi"] == CQP_QUANTIZER, element
        assert spec.props["qpp"] == CQP_QUANTIZER, element
        assert spec.props["rate-control"] == "cqp", element
        assert "bitrate" not in spec.props, element


def test_qsv_cqp_sets_its_own_quantizer_properties():
    """qsvh264enc is absent on this machine, so `qp-i`/`qp-p` come from the
    plugin's documentation, not introspection. _encoder_runs() rejects the
    encoder at runtime if they are wrong."""
    spec = build_spec("qsvh264enc", _cqp_config(), {RateControl.CQP})
    assert spec.props["qp-i"] == CQP_QUANTIZER
    assert spec.props["qp-p"] == CQP_QUANTIZER
    assert "bitrate" not in spec.props


def test_degrading_to_cqp_still_yields_a_quality_target():
    """The regression: CBR unavailable → degrade to CQP → no QP set at all.

    An Intel user on vah264lpenc reaches this path on every single connection.
    """
    spec = build_spec("vah264lpenc", _cbr_config(), {RateControl.CQP})
    assert spec.rate_control == RateControl.CQP
    assert spec.props["qpi"] == CQP_QUANTIZER
    assert spec.props["qpp"] == CQP_QUANTIZER


def test_all_cqp_capable_adapters_agree_on_the_same_quantizer():
    """x264 and nvenc already used 21; the rest must not drift from it."""
    for element, names in CQP_PROPERTY.items():
        spec = build_spec(element, _cqp_config(), {RateControl.CQP})
        assert spec is not None, element
        for name in names:
            assert spec.props[name] == CQP_QUANTIZER, f"{element}.{name}"


def test_cqp_property_names_are_not_shared_across_vendors():
    """Guessing one name across all encoders silently sets nothing on most of
    them — the same trap RC_PROPERTY exists to avoid."""
    assert CQP_PROPERTY["x264enc"] == ("quantizer",)
    assert CQP_PROPERTY["nvh264enc"] == ("qp-const",)
    assert CQP_PROPERTY["vaapih264enc"] == ("init-qp",)
    assert CQP_PROPERTY["vah264lpenc"] == ("qpi", "qpp")
    assert CQP_PROPERTY["qsvh264enc"] == ("qp-i", "qp-p")


def test_cbr_mode_sets_no_quantizer_anywhere():
    """A quantizer alongside a bitrate target is contradictory."""
    for element, names in CQP_PROPERTY.items():
        spec = build_spec(element, _cbr_config(), {RateControl.CBR})
        for name in names:
            assert name not in spec.props, f"{element}.{name} set in CBR mode"
