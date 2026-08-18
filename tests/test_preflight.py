from server.core.preflight import (
    H264_ENCODER_CANDIDATES,
    format_preflight,
    probe_encoders,
)


def test_candidates_prefer_hardware_before_software():
    """Software encoders are the last resort, never probed first."""
    order = list(H264_ENCODER_CANDIDATES)
    assert order.index("nvh264enc") < order.index("x264enc")
    assert order.index("vaapih264enc") < order.index("x264enc")
    assert order[-1] == "openh264enc"


def test_probe_returns_only_present_elements_in_priority_order():
    present = {"x264enc", "nvh264enc"}
    found = probe_encoders(finder=lambda name: name in present or None)
    assert found == ["nvh264enc", "x264enc"]


def test_probe_returns_empty_when_nothing_available():
    assert probe_encoders(finder=lambda name: None) == []


def test_probe_accepts_explicit_candidate_list():
    found = probe_encoders(finder=lambda name: True, candidates=("a", "b"))
    assert found == ["a", "b"]


def test_format_preflight_reports_version_and_encoders():
    text = format_preflight("1.24.2", "/usr/lib/gstreamer-1.0", ["nvh264enc"])
    assert "1.24.2" in text
    assert "/usr/lib/gstreamer-1.0" in text
    assert "nvh264enc" in text


def test_format_preflight_warns_when_no_encoder_found():
    text = format_preflight("1.24.2", "/usr/lib/gstreamer-1.0", [])
    assert "WARNING" in text
