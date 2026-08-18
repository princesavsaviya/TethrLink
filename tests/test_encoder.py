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
