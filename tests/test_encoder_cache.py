"""Tests for reconstructing an EncoderSpec from a disk-cached encoder record.

This is the pure-Python half of select_encoder() (server_core.py): given
whatever dict ProfileStore.get_encoder() handed back, turn it into an
EncoderSpec, or into None if the record is malformed. No GStreamer element
is instantiated and no GPU is touched here — see _encoder_spec_from_cache's
docstring for why that split exists.

A cached `props` field that isn't a mapping (a list, a string, an int) used
to reach a bare `dict(...)` call and raise — `dict(['a', 'b', 'c'])` raises
ValueError — which happened before the trust-but-verify re-run and before
the probe loop that would otherwise have overwritten the bad entry. That
raise broke every subsequent client connection identically until someone
found and deleted the cache file by hand. These tests pin the fix: any
malformed record must degrade to None (→ a normal re-probe), never raise.
"""

import logging

import pytest

from server.core.encoder import EncoderConfig, EncoderSpec, RateControl, default_bitrate_kbps
from server.core.profiles import ProfileStore
from server.core import server_core
from server.core.server_core import (
    _encoder_spec_from_cache,
    gstreamer_fingerprint,
    select_encoder,
)


# ── well-formed record ────────────────────────────────────────────────────

def test_well_formed_record_round_trips_every_field():
    record = {
        "element": "vah264lpenc",
        "is_hardware": True,
        "rate_control": "cqp",
        "props": {"qpi": "21", "qpp": "21", "key-int-max": "45"},
    }
    spec = _encoder_spec_from_cache(record)
    assert spec == EncoderSpec(
        element="vah264lpenc",
        is_hardware=True,
        rate_control="cqp",
        props={"qpi": "21", "qpp": "21", "key-int-max": "45"},
    )


# ── malformed `props` — the reviewer's repro ─────────────────────────────

def test_props_as_a_list_yields_none_instead_of_raising():
    assert _encoder_spec_from_cache({
        "element": "x264enc", "is_hardware": False,
        "rate_control": "cbr", "props": ["a", "b", "c"],
    }) is None


def test_props_as_a_string_yields_none_instead_of_raising():
    assert _encoder_spec_from_cache({
        "element": "x264enc", "is_hardware": False,
        "rate_control": "cbr", "props": "not-a-mapping",
    }) is None


def test_props_as_an_integer_yields_none_instead_of_raising():
    assert _encoder_spec_from_cache({
        "element": "x264enc", "is_hardware": False,
        "rate_control": "cbr", "props": 42,
    }) is None


# ── missing / absent fields ──────────────────────────────────────────────

def test_missing_props_defaults_to_empty_dict():
    """Absence is not malformed — this was already the pre-fix behaviour
    (`cached.get("props") or {}`) and must keep working: an encoder that
    needs no extra properties is a perfectly ordinary cached record."""
    spec = _encoder_spec_from_cache({
        "element": "x264enc", "is_hardware": False, "rate_control": "cbr",
    })
    assert spec == EncoderSpec(
        element="x264enc", is_hardware=False, rate_control="cbr", props={},
    )


def test_missing_element_yields_none():
    assert _encoder_spec_from_cache({
        "is_hardware": True, "rate_control": "cqp", "props": {},
    }) is None


def test_empty_dict_yields_none():
    assert _encoder_spec_from_cache({}) is None


# ── non-dict input ────────────────────────────────────────────────────────

def test_non_dict_argument_yields_none():
    assert _encoder_spec_from_cache(["not", "a", "dict"]) is None


# ── select_encoder(): a cache hit must rebuild resolution-dependent props ──
#
# The bug this reproduces, verbatim from hardware testing: a stale disk
# cache entry recorded `bitrate: '16410'` from an earlier, differently-sized
# session. A later connection at a different resolution hit the cache,
# replayed those props unchanged, and the pipeline ran at 16410 kbps while
# the log — computed from the freshly-derived bitrate — claimed 8000. The
# pipeline and the log disagreed, and the pipeline was the wrong one.
#
# These run the real encoder (this dev machine has a working nvh264enc) —
# select_encoder's trust-but-verify step actually instantiates and runs the
# pipeline, so there is no way to observe the fix without doing that for
# real.

@pytest.fixture
def isolated_profile_cache(tmp_path, monkeypatch):
    """Point ProfileStore() — which select_encoder() constructs internally
    with no path argument — at an isolated temp directory, and drop the
    process-lifetime in-memory encoder cache so this test genuinely
    exercises the disk-cache-hit path instead of short-circuiting on a key
    some earlier test already populated.
    """
    monkeypatch.delenv("SNAP_USER_COMMON", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    server_core._ENCODER_CACHE.clear()
    yield tmp_path
    server_core._ENCODER_CACHE.clear()


def _seed_stale_cache_entry(fingerprint, *, element="nvh264enc", stale_bitrate="16410"):
    """Write a disk cache record shaped exactly like the real bug report:
    a resolved encoder choice whose props were rendered for some earlier,
    different-resolution session.
    """
    store = ProfileStore()
    store.load()
    store.set_encoder(fingerprint, {
        "element": element,
        "is_hardware": True,
        "rate_control": RateControl.CBR,
        "props": {
            "gop-size": "30",
            "bframes": "0",
            "zerolatency": "true",
            "rc-mode": "cbr-ld-hq",
            "bitrate": stale_bitrate,
        },
    })
    store.save()


def test_cache_hit_rebuilds_bitrate_for_the_new_resolution(isolated_profile_cache, caplog):
    fingerprint = gstreamer_fingerprint()
    _seed_stale_cache_entry(fingerprint)

    width, height, fps = 1730, 1080, 30
    expected_bitrate = default_bitrate_kbps(width, height, fps)
    # Sanity check this test is actually exercising the bug's exact
    # scenario: the freshly-derived bitrate must differ from the stale one.
    assert expected_bitrate != 16410

    with caplog.at_level(logging.INFO, logger="TethrLink"):
        spec = select_encoder(
            EncoderConfig(
                bitrate_kbps=expected_bitrate,
                gop_length=fps,
                rate_control=RateControl.CBR,
            ),
            width, height,
        )

    assert spec is not None
    # The expensive half of the cached decision (element, rate-control) is
    # still reused — this is what going through the cache is for.
    assert spec.element == "nvh264enc"
    assert any("Encoder from cache" in r.message for r in caplog.records)
    # The resolution-dependent half must NOT be the stale replayed value —
    # it must be freshly derived for THIS call's resolution.
    assert spec.props.get("bitrate") == str(expected_bitrate)
    assert spec.props.get("bitrate") != "16410"


def test_cache_hit_at_a_different_resolution_again_produces_that_resolutions_bitrate(
    isolated_profile_cache,
):
    """Same cached record, a second distinct resolution — pins that the
    rebuild tracks whatever the *current* call asks for, not just
    "different from the one stale example"."""
    fingerprint = gstreamer_fingerprint()
    _seed_stale_cache_entry(fingerprint)

    width, height, fps = 2560, 1440, 30
    expected_bitrate = default_bitrate_kbps(width, height, fps)
    assert expected_bitrate not in (16410, 8000)

    spec = select_encoder(
        EncoderConfig(
            bitrate_kbps=expected_bitrate,
            gop_length=fps,
            rate_control=RateControl.CBR,
        ),
        width, height,
    )

    assert spec is not None
    assert spec.props.get("bitrate") == str(expected_bitrate)


def test_cache_hit_pipeline_fragment_matches_what_would_be_logged(isolated_profile_cache):
    """The end-to-end honesty check: whatever bitrate ends up in the
    pipeline fragment is the same number describe_h264_encoding() would
    report — no more "log says 8000, pipeline runs at 16410"."""
    from server.core.server_core import describe_h264_encoding

    fingerprint = gstreamer_fingerprint()
    _seed_stale_cache_entry(fingerprint)

    width, height, fps = 1730, 1080, 30
    bitrate_kbps = default_bitrate_kbps(width, height, fps)
    bitrate_source = "derived from resolution/fps"

    spec = select_encoder(
        EncoderConfig(bitrate_kbps=bitrate_kbps, gop_length=fps, rate_control=RateControl.CBR),
        width, height,
    )
    fragment = spec.to_pipeline_fragment()
    log_line = describe_h264_encoding(spec, bitrate_kbps, bitrate_source)

    assert f"bitrate={bitrate_kbps}" in fragment
    assert f"{bitrate_kbps} kbps" in log_line
    assert "16410" not in fragment
    assert "16410" not in log_line
