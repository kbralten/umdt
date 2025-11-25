"""Tests for umdt.utils.address module."""

import pytest
from umdt.utils.address import (
    parse_address,
    format_address,
    parse_address_range,
)


class TestParseAddress:
    """Tests for parse_address function."""

    def test_decimal_address(self):
        addr, was_hex = parse_address("100")
        assert addr == 100
        assert was_hex is False

    def test_hex_address_lowercase(self):
        addr, was_hex = parse_address("0x64")
        assert addr == 100
        assert was_hex is True

    def test_hex_address_uppercase(self):
        addr, was_hex = parse_address("0X64")
        assert addr == 100
        assert was_hex is True

    def test_hex_mixed_case(self):
        addr, was_hex = parse_address("0xABcd")
        assert addr == 0xABCD
        assert was_hex is True

    def test_zero(self):
        addr, was_hex = parse_address("0")
        assert addr == 0
        assert was_hex is False

    def test_hex_zero(self):
        addr, was_hex = parse_address("0x0")
        assert addr == 0
        assert was_hex is True

    def test_whitespace_stripped(self):
        addr, was_hex = parse_address("  100  ")
        assert addr == 100
        assert was_hex is False

    def test_large_address(self):
        addr, was_hex = parse_address("65535")
        assert addr == 65535
        assert was_hex is False

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            parse_address("")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            parse_address("   ")

    def test_invalid_format_raises(self):
        with pytest.raises(ValueError, match="Invalid address"):
            parse_address("abc")

    def test_invalid_hex_raises(self):
        with pytest.raises(ValueError, match="Invalid address"):
            parse_address("0xZZZ")


class TestFormatAddress:
    """Tests for format_address function."""

    def test_decimal_format(self):
        assert format_address(100, as_hex=False) == "100"
        assert format_address(65535, as_hex=False) == "65535"

    def test_hex_format(self):
        assert format_address(100, as_hex=True) == "0x64"
        assert format_address(255, as_hex=True) == "0xff"

    def test_zero_decimal(self):
        assert format_address(0, as_hex=False) == "0"

    def test_zero_hex(self):
        assert format_address(0, as_hex=True) == "0x0"


class TestParseAddressRange:
    """Tests for parse_address_range function."""

    def test_decimal_range(self):
        start, end, use_hex = parse_address_range("0", "100")
        assert start == 0
        assert end == 100
        assert use_hex is False

    def test_hex_range(self):
        start, end, use_hex = parse_address_range("0x00", "0x64")
        assert start == 0
        assert end == 100
        assert use_hex is True

    def test_mixed_range_start_hex(self):
        # If either is hex, output should be hex
        start, end, use_hex = parse_address_range("0x00", "100")
        assert start == 0
        assert end == 100
        assert use_hex is True

    def test_mixed_range_end_hex(self):
        start, end, use_hex = parse_address_range("0", "0x64")
        assert start == 0
        assert end == 100
        assert use_hex is True

    def test_same_address(self):
        start, end, use_hex = parse_address_range("50", "50")
        assert start == 50
        assert end == 50
        assert use_hex is False

    def test_start_greater_than_end_raises(self):
        with pytest.raises(ValueError, match="must be <="):
            parse_address_range("100", "50")

    def test_invalid_start_raises(self):
        with pytest.raises(ValueError, match="Invalid address"):
            parse_address_range("abc", "100")

    def test_invalid_end_raises(self):
        with pytest.raises(ValueError, match="Invalid address"):
            parse_address_range("0", "xyz")
