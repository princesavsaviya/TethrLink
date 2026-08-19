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

from server.core.encoder import (
    EncoderConfig,
    EncoderSpec,
    HARDWARE_ELEMENTS,
    RateControl,
    default_bitrate_kbps,
)
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


def _seed_stale_cache_entry(
    fingerprint, *,
    element="nvh264enc",
    rate_control=RateControl.CBR,
    stale_bitrate="16410",
):
    """Write a disk cache record shaped exactly like the real bug report:
    a resolved encoder choice whose props were rendered for some earlier,
    different-resolution session.

    `element` and `rate_control` are parameters, not hardcoded, precisely so
    tests can fake "whatever this machine's driver previously verified"
    without depending on what the machine running the test actually has.
    """
    store = ProfileStore()
    store.load()
    store.set_encoder(fingerprint, {
        "element": element,
        "is_hardware": element in HARDWARE_ELEMENTS,
        "rate_control": rate_control,
        "props": {
            "gop-size": "30",
            "bframes": "0",
            "zerolatency": "true",
            "rc-mode": "cbr-ld-hq",
            "bitrate": stale_bitrate,
        },
    })
    store.save()


# The bug this reproduces, verbatim from hardware testing: a stale disk
# cache entry recorded `bitrate: '16410'` from an earlier, differently-sized
# session. A later connection at a different resolution hit the cache,
# replayed those props unchanged, and the pipeline ran at 16410 kbps while
# the log — computed from the freshly-derived bitrate — claimed 8000. The
# pipeline and the log disagreed, and the pipeline was the wrong one.
#
# select_encoder()'s trust-but-verify step instantiates and actually runs a
# GStreamer pipeline (_encoder_runs) before trusting a cached choice, and
# consults probe_rate_controls() when it has to fall back to a fresh probe.
# Which encoders that succeeds for is a fact about the machine — GPU vendor,
# driver version, even which kernel module happens to be loaded right now —
# not a fact about this code. Pinning these tests to "nvh264enc verifies
# here" made them pass or fail depending on whose machine ran them: they
# fail outright the moment this dev machine's NVIDIA driver package drifts
# from its kernel module (nvidia-smi starts reporting a version mismatch and
# nvh264enc probes zero rate-control modes), and they would fail identically
# on any machine with no NVIDIA GPU at all — most machines.
#
# What is actually under test is pure Python: does select_encoder() reuse
# the cached element/rate-control instead of re-probing, and does it rebuild
# props fresh from the *current* call's config instead of replaying the
# stale cached ones? Neither question depends on which encoders this
# machine's driver happens to verify, so `_encoder_runs` is monkeypatched to
# always succeed and `probe_rate_controls` is monkeypatched to blow up if
# called at all — a cache hit is supposed to skip probing entirely, so a
# regression that makes it re-probe should fail loudly here rather than
# quietly passing on whatever this machine's driver supports. This is
# exactly what `build_spec()`'s `available_rate_controls` parameter exists
# for: driving the outcome from injected data instead of real hardware.

def _unreachable_probe(element):
    raise AssertionError(
        f"probe_rate_controls({element!r}) was called on a cache hit — a "
        f"cached choice must be reused, not re-probed"
    )


@pytest.fixture
def always_verified(monkeypatch):
    """Stub the one call in select_encoder()'s cache-hit path that touches
    real GStreamer/GPU state, so the rebuild logic is exercised purely in
    Python, independent of what this machine's driver can actually run.
    """
    monkeypatch.setattr(server_core, "_encoder_runs", lambda spec, width, height: True)
    monkeypatch.setattr(server_core, "probe_rate_controls", _unreachable_probe)


@pytest.mark.parametrize("element", ["nvh264enc", "vah264enc", "x264enc"])
def test_cache_hit_rebuilds_bitrate_for_the_new_resolution(
    isolated_profile_cache, always_verified, caplog, element,
):
    """Parametrized over a hardware NVENC-style element, a hardware VA
    element, and a pure-software element: the outcome tracks whatever the
    test fakes as the cached choice, never what this particular machine's
    GPU actually offers.
    """
    fingerprint = gstreamer_fingerprint()
    _seed_stale_cache_entry(fingerprint, element=element, rate_control=RateControl.CBR)

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
    assert spec.element == element
    assert any("Encoder from cache" in r.message for r in caplog.records)
    # The resolution-dependent half must NOT be the stale replayed value —
    # it must be freshly derived for THIS call's resolution. CBR is what was
    # cached, so a real bitrate property is the correct thing to assert on
    # (a CQP-only encoder legitimately has none — see the dedicated CQP test
    # below rather than loosening this one to tolerate that).
    assert spec.props.get("bitrate") == str(expected_bitrate)
    assert spec.props.get("bitrate") != "16410"


def test_cache_hit_at_a_different_resolution_again_produces_that_resolutions_bitrate(
    isolated_profile_cache, always_verified,
):
    """Same cached record, a second distinct resolution — pins that the
    rebuild tracks whatever the *current* call asks for, not just
    "different from the one stale example"."""
    fingerprint = gstreamer_fingerprint()
    _seed_stale_cache_entry(fingerprint, rate_control=RateControl.CBR)

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


def test_cache_hit_pipeline_fragment_matches_what_would_be_logged(
    isolated_profile_cache, always_verified,
):
    """The end-to-end honesty check: whatever bitrate ends up in the
    pipeline fragment is the same number describe_h264_encoding() would
    report — no more "log says 8000, pipeline runs at 16410"."""
    from server.core.server_core import describe_h264_encoding

    fingerprint = gstreamer_fingerprint()
    _seed_stale_cache_entry(fingerprint, rate_control=RateControl.CBR)

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


def test_cache_hit_rebuild_of_a_cqp_only_encoder_has_no_bitrate(
    isolated_profile_cache, always_verified,
):
    """The mirror image of the CBR tests above. A machine whose cached
    choice only ever verified in CQP mode — exactly what happens on an
    Intel iGPU, where vah264lpenc's rate-control enum offers no cbr/vbr
    value at all — must legitimately rebuild to an encoder with no
    `bitrate` property. This is asserted deliberately, rather than by
    loosening the CBR tests above to tolerate a None bitrate, so the CQP
    degrade path stays under real test pressure.
    """
    fingerprint = gstreamer_fingerprint()
    _seed_stale_cache_entry(fingerprint, element="vah264lpenc", rate_control=RateControl.CQP)

    width, height, fps = 1730, 1080, 30
    spec = select_encoder(
        EncoderConfig(
            # Requested as CBR; the cached record only ever verified CQP, so
            # the rebuild must follow the cached rate-control, not this one.
            bitrate_kbps=default_bitrate_kbps(width, height, fps),
            gop_length=fps,
            rate_control=RateControl.CBR,
        ),
        width, height,
    )

    assert spec is not None
    assert spec.element == "vah264lpenc"
    assert spec.rate_control == RateControl.CQP
    assert "bitrate" not in spec.props
    assert spec.props.get("qpi") == "21"
    assert spec.props.get("qpp") == "21"


# ── select_encoder(): a None selection is never persisted ────────────────
#
# Caching "no usable encoder" would latch that verdict for the rest of the
# process's life — a transient failure (a momentarily busy GPU, a slow
# _encoder_runs bus wait) would then need an app restart to ever get
# encoding again. select_encoder() only writes to the in-memory cache and
# the disk store when it actually found something (see the comment above
# the cache write in server_core.py). These tests pin that both stay
# untouched when it doesn't, using a GPU-free failure mode — every
# candidate's rate-control probe reports nothing usable — rather than
# depending on this machine genuinely having no working encoder.

def _no_rate_controls(element):
    return set()


def _unreachable_run(spec, width, height):
    raise AssertionError(
        f"_encoder_runs() was called for {spec.element!r} — build_spec() "
        f"should already have rejected every candidate before a pipeline "
        f"is ever instantiated"
    )


def test_failed_selection_is_never_cached_in_memory_or_on_disk(
    isolated_profile_cache, monkeypatch,
):
    monkeypatch.setattr(server_core, "probe_rate_controls", _no_rate_controls)
    monkeypatch.setattr(server_core, "_encoder_runs", _unreachable_run)

    config = EncoderConfig(bitrate_kbps=8000, gop_length=30, rate_control=RateControl.CBR)
    width, height = 1920, 1080

    assert select_encoder(config, width, height) is None

    key = (config.rate_control, config.gop_length, config.bitrate_kbps,
           config.low_latency, width, height)
    assert key not in server_core._ENCODER_CACHE

    fingerprint = gstreamer_fingerprint()
    store = ProfileStore()
    store.load()
    assert store.get_encoder(fingerprint) is None


def test_failed_selection_re_probes_next_time_instead_of_staying_failed(
    isolated_profile_cache, monkeypatch,
):
    """The flip side of not caching None: a later call for the same config
    must try again rather than silently inheriting the earlier failure —
    the property that makes a transient probe failure recoverable without
    an app restart."""
    calls = []

    def counting_probe(element):
        calls.append(element)
        return set()

    monkeypatch.setattr(server_core, "probe_rate_controls", counting_probe)
    monkeypatch.setattr(server_core, "_encoder_runs", _unreachable_run)

    config = EncoderConfig(bitrate_kbps=8000, gop_length=30, rate_control=RateControl.CBR)
    width, height = 1920, 1080

    assert select_encoder(config, width, height) is None
    first_round = len(calls)
    assert first_round > 0  # every CANDIDATES entry was actually tried

    assert select_encoder(config, width, height) is None
    assert len(calls) == first_round * 2  # tried again, not skipped
