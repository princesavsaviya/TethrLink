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

from server.core.encoder import EncoderSpec
from server.core.server_core import _encoder_spec_from_cache


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
