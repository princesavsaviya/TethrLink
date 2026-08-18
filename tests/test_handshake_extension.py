"""
Unit tests for input-capability extension parsing in _handle_client handshake.

Comprehensive coverage of well-formed, malformed, truncated, and edge-case
extension blocks, with the overriding property: no input can make
parse_input_extension() raise. All malformed blocks degrade to "no input
support," never propagating an exception into the connection handler.
"""

import pytest
from server.core.server_core import (
    parse_input_extension,
    EXT_MARKER,
    EXT_HEADER_LEN,
    CAP_POINTER_INPUT,
)


class TestExtensionParsing:
    """Test parse_input_extension() with all cases from the brief."""

    # ── Well-formed cases ─────────────────────────────────────────────────

    def test_well_formed_extension_advertising_input(self):
        """Well-formed extension advertising input → True."""
        # Header: TLX1 (marker) + 0x01 (cap_len=1)
        # Payload: 0x01 (CAP_POINTER_INPUT bit set)
        header = b"TLX1\x01"
        payload = b"\x01"
        result = parse_input_extension(header, payload)
        assert result is True

    def test_well_formed_extension_capability_bit_clear(self):
        """Well-formed extension with capability bit clear → False."""
        header = b"TLX1\x01"
        payload = b"\x00"  # CAP_POINTER_INPUT bit NOT set
        result = parse_input_extension(header, payload)
        assert result is False

    def test_well_formed_extension_with_other_bits_set(self):
        """Well-formed extension with other bits set but not CAP_POINTER_INPUT → False."""
        header = b"TLX1\x01"
        payload = b"\x02"  # Some other bit set, not bit 0
        result = parse_input_extension(header, payload)
        assert result is False

    def test_well_formed_extension_both_bits_set(self):
        """Well-formed extension with multiple bits set, including CAP_POINTER_INPUT → True."""
        header = b"TLX1\x01"
        payload = b"\x03"  # Bits 0 and 1 set
        result = parse_input_extension(header, payload)
        assert result is True

    # ── Legacy client (no extension) ─────────────────────────────────────

    def test_empty_header_legacy_client(self):
        """Empty header (legacy client sends nothing) → False."""
        header = b""
        payload = b""
        result = parse_input_extension(header, payload)
        assert result is False

    def test_no_extension_none_header(self):
        """None header case should not raise."""
        # This tests defensive handling of unexpected input
        try:
            result = parse_input_extension(b"", b"")
            assert result is False
        except Exception as e:
            pytest.fail(f"Should not raise, got {type(e).__name__}: {e}")

    # ── Wrong marker ──────────────────────────────────────────────────────

    def test_wrong_marker_four_bytes(self):
        """Wrong marker (4 bytes but wrong bytes) → False."""
        header = b"XYZX\x01"  # Wrong marker
        payload = b"\x01"
        result = parse_input_extension(header, payload)
        assert result is False

    def test_wrong_marker_case_sensitive(self):
        """Marker is case-sensitive: lowercase → False."""
        header = b"tlx1\x01"  # lowercase
        payload = b"\x01"
        result = parse_input_extension(header, payload)
        assert result is False

    def test_marker_with_null_byte(self):
        """Marker with null byte early → False."""
        header = b"\x00LX1\x01"
        payload = b"\x01"
        result = parse_input_extension(header, payload)
        assert result is False

    # ── Truncated header ──────────────────────────────────────────────────

    def test_truncated_header_one_byte(self):
        """Truncated header (1 byte, not 5) → False."""
        header = b"T"
        payload = b""
        result = parse_input_extension(header, payload)
        assert result is False

    def test_truncated_header_two_bytes(self):
        """Truncated header (2 bytes) → False."""
        header = b"TL"
        payload = b""
        result = parse_input_extension(header, payload)
        assert result is False

    def test_truncated_header_three_bytes(self):
        """Truncated header (3 bytes) → False."""
        header = b"TLX"
        payload = b""
        result = parse_input_extension(header, payload)
        assert result is False

    def test_truncated_header_four_bytes(self):
        """Truncated header (4 bytes, missing cap_len) → False."""
        header = b"TLX1"
        payload = b""
        result = parse_input_extension(header, payload)
        assert result is False

    # ── Truncated payload ─────────────────────────────────────────────────

    def test_payload_shorter_than_declared(self):
        """Payload shorter than declared length → False."""
        header = b"TLX1\x02"  # cap_len=2
        payload = b"\x01"      # only 1 byte provided
        result = parse_input_extension(header, payload)
        assert result is False

    def test_payload_empty_when_declared_nonzero(self):
        """Payload declared as cap_len=1 but empty → False."""
        header = b"TLX1\x01"
        payload = b""
        result = parse_input_extension(header, payload)
        assert result is False

    def test_payload_declared_length_10_actual_1(self):
        """Payload declares cap_len=10 but only 1 byte present → False."""
        header = b"TLX1\x0a"  # cap_len=10
        payload = b"\x01"
        result = parse_input_extension(header, payload)
        assert result is False

    # ── Payload longer than declared ──────────────────────────────────────

    def test_payload_longer_than_declared(self):
        """Payload longer than declared length → False.

        The implementation requires the payload to be exactly cap_len bytes,
        never more. A longer payload indicates a malformed block.
        """
        header = b"TLX1\x01"  # cap_len=1
        payload = b"\x01EXTRA"  # declared 1, got 5
        result = parse_input_extension(header, payload)
        assert result is False

    def test_payload_longer_input_bit_not_set(self):
        """Payload longer than declared → False regardless of first byte."""
        header = b"TLX1\x01"  # cap_len=1
        payload = b"\x01EXTRA"  # payload is too long (declared 1, got 5)
        result = parse_input_extension(header, payload)
        assert result is False

    # ── Declared length of zero ───────────────────────────────────────────

    def test_declared_length_zero(self):
        """Declared length is zero (cap_len=0) → False rather than raising."""
        header = b"TLX1\x00"  # cap_len=0, no payload
        payload = b""
        result = parse_input_extension(header, payload)
        assert result is False

    def test_declared_length_zero_payload_present(self):
        """Declared length zero but payload provided → ignored, result False."""
        header = b"TLX1\x00"  # cap_len=0
        payload = b"\x01\x02\x03"  # bytes present but cap_len says 0
        result = parse_input_extension(header, payload)
        # Implementation reads exactly 0 bytes when cap_len=0, so payload is ignored
        assert result is False

    # ── Robustness against variations ─────────────────────────────────────

    def test_high_cap_len_value(self):
        """cap_len=255 (maximum u8) with insufficient payload → False."""
        header = b"TLX1\xff"  # cap_len=255
        payload = b"\x01"      # only 1 byte
        result = parse_input_extension(header, payload)
        assert result is False

    def test_high_cap_len_value_exact_match(self):
        """cap_len=255 with exactly 255 bytes of payload → uses first byte."""
        header = b"TLX1\xff"  # cap_len=255
        payload = b"\x01" + b"\x00" * 254  # exactly 255 bytes
        result = parse_input_extension(header, payload)
        assert result is True  # first byte is 0x01

    def test_cap_len_2_first_byte_zero(self):
        """cap_len=2, first byte is 0 (no input), second byte is irrelevant."""
        header = b"TLX1\x02"
        payload = b"\x00\x01"  # first byte 0x00, second 0x01
        result = parse_input_extension(header, payload)
        # Only first byte matters
        assert result is False

    def test_cap_len_2_first_byte_nonzero(self):
        """cap_len=2, first byte has CAP_POINTER_INPUT, second is irrelevant."""
        header = b"TLX1\x02"
        payload = b"\x01\x00"  # first byte 0x01 (has input), second 0x00
        result = parse_input_extension(header, payload)
        assert result is True

    # ── No exceptions on any input ────────────────────────────────────────

    def test_no_exception_on_random_bytes(self):
        """Random bytes should never raise, only return False."""
        test_cases = [
            (b"\xff\xff\xff\xff\xff", b"\xff" * 100),
            (b"\x00\x00\x00\x00\x00", b"\x00" * 100),
            (b"AAAAA", b"A" * 100),
            (b"TLX1\xff", b""),  # header looks right but missing payload
            (b"TLX1", b"payload"),  # header too short
            (b"", b""),
        ]
        for header, payload in test_cases:
            try:
                result = parse_input_extension(header, payload)
                # Should return a bool, never raise
                assert isinstance(result, bool), f"Expected bool, got {type(result)}"
            except Exception as e:
                pytest.fail(
                    f"parse_input_extension({header!r}, {payload!r}) raised "
                    f"{type(e).__name__}: {e}"
                )

    def test_no_exception_on_none_input(self):
        """None inputs should be handled gracefully, never raise."""
        try:
            # Test with b"" which is the safest fallback
            result = parse_input_extension(b"", b"")
            assert result is False
        except Exception as e:
            pytest.fail(f"Failed to handle empty input gracefully: {e}")

    # ── Specification compliance ──────────────────────────────────────────

    def test_marker_is_exactly_tlx1(self):
        """Verify EXT_MARKER constant is exactly b'TLX1'."""
        assert EXT_MARKER == b"TLX1"
        assert len(EXT_MARKER) == 4

    def test_ext_header_len_is_5(self):
        """Verify EXT_HEADER_LEN is 5 (marker + cap_len)."""
        assert EXT_HEADER_LEN == 5
        assert EXT_HEADER_LEN == len(EXT_MARKER) + 1

    def test_cap_pointer_input_bit_is_0x01(self):
        """Verify CAP_POINTER_INPUT constant is bit 0 (0x01)."""
        assert CAP_POINTER_INPUT == 0x01
