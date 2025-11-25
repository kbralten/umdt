"""Tests for umdt.utils.encoding module."""

import pytest
import math
import struct
from umdt.utils.encoding import (
    encode_int16,
    encode_int32,
    encode_float16,
    encode_float32,
    encode_value,
    normalize_endian,
    EncodingError,
)


class TestEncodeInt16:
    """Tests for encode_int16 function."""

    def test_zero(self):
        assert encode_int16(0) == [0]

    def test_positive_value(self):
        assert encode_int16(100) == [100]
        assert encode_int16(0x1234) == [0x1234]

    def test_max_unsigned(self):
        assert encode_int16(65535) == [65535]

    def test_signed_positive(self):
        assert encode_int16(100, signed=True) == [100]

    def test_signed_negative(self):
        # -1 should be 0xFFFF
        assert encode_int16(-1, signed=True) == [0xFFFF]
        # -32768 should be 0x8000
        assert encode_int16(-32768, signed=True) == [0x8000]

    def test_little_endian(self):
        # 0x1234 in little-endian is 0x3412
        result = encode_int16(0x1234, endian="little")
        assert result == [0x3412]

    def test_out_of_range_unsigned(self):
        with pytest.raises(EncodingError, match="out of 16-bit unsigned"):
            encode_int16(65536)

    def test_out_of_range_signed_positive(self):
        with pytest.raises(EncodingError, match="out of 16-bit signed"):
            encode_int16(32768, signed=True)

    def test_out_of_range_signed_negative(self):
        with pytest.raises(EncodingError, match="out of 16-bit signed"):
            encode_int16(-32769, signed=True)


class TestEncodeInt32:
    """Tests for encode_int32 function."""

    def test_zero(self):
        assert encode_int32(0) == [0, 0]

    def test_positive_value(self):
        # 0x12345678 should be [0x1234, 0x5678]
        assert encode_int32(0x12345678) == [0x1234, 0x5678]

    def test_max_unsigned(self):
        assert encode_int32(0xFFFFFFFF) == [0xFFFF, 0xFFFF]

    def test_signed_negative(self):
        # -1 should be 0xFFFFFFFF -> [0xFFFF, 0xFFFF]
        assert encode_int32(-1, signed=True) == [0xFFFF, 0xFFFF]

    def test_little_endian(self):
        # 0x12345678 in little-endian is DCBA = 0x78563412
        result = encode_int32(0x12345678, endian="little")
        assert result == [0x7856, 0x3412]

    def test_mid_big_endian(self):
        # 0x12345678 in mid-big (CDAB) is 0x56781234
        result = encode_int32(0x12345678, endian="mid-big")
        assert result == [0x5678, 0x1234]

    def test_mid_little_endian(self):
        # 0x12345678 in mid-little (BADC) is 0x34127856
        result = encode_int32(0x12345678, endian="mid-little")
        assert result == [0x3412, 0x7856]

    def test_out_of_range(self):
        with pytest.raises(EncodingError, match="out of 32-bit unsigned"):
            encode_int32(0x100000000)


class TestEncodeFloat16:
    """Tests for encode_float16 function."""

    def test_zero(self):
        result = encode_float16(0.0)
        assert result == [0]

    def test_one(self):
        # 1.0 in float16 is 0x3C00
        result = encode_float16(1.0)
        assert result == [0x3C00]

    def test_negative_one(self):
        # -1.0 in float16 is 0xBC00
        result = encode_float16(-1.0)
        assert result == [0xBC00]

    def test_infinity(self):
        result = encode_float16(float('inf'))
        assert result == [0x7C00]

    def test_negative_infinity(self):
        result = encode_float16(float('-inf'))
        assert result == [0xFC00]

    def test_nan(self):
        result = encode_float16(float('nan'))
        # NaN representation
        assert (result[0] & 0x7C00) == 0x7C00  # Exponent all 1s
        assert (result[0] & 0x03FF) != 0  # Non-zero mantissa

    def test_little_endian(self):
        # 1.0 (0x3C00) in little endian is 0x003C
        result = encode_float16(1.0, endian="little")
        assert result == [0x003C]


class TestEncodeFloat32:
    """Tests for encode_float32 function."""

    def test_zero(self):
        result = encode_float32(0.0)
        assert result == [0, 0]

    def test_one(self):
        # 1.0 in float32 is 0x3F800000
        result = encode_float32(1.0)
        assert result == [0x3F80, 0x0000]

    def test_ten(self):
        # 10.0 in float32 is 0x41200000
        result = encode_float32(10.0)
        assert result == [0x4120, 0x0000]

    def test_negative(self):
        result = encode_float32(-1.0)
        # -1.0 is 0xBF800000
        assert result == [0xBF80, 0x0000]

    def test_little_endian(self):
        # 1.0 (0x3F800000) in little-endian
        result = encode_float32(1.0, endian="little")
        # 0x3F800000 reversed is 0x0000803F
        assert result == [0x0000, 0x803F]

    def test_mid_big_endian(self):
        # 1.0 (0x3F800000) in mid-big (CDAB)
        result = encode_float32(1.0, endian="mid-big")
        assert result == [0x0000, 0x3F80]

    def test_mid_little_endian(self):
        # 1.0 (0x3F800000) in mid-little (BADC)
        result = encode_float32(1.0, endian="mid-little")
        assert result == [0x803F, 0x0000]


class TestEncodeValue:
    """Tests for the main encode_value entry point."""

    def test_simple_integer(self):
        regs, signed = encode_value("100")
        assert regs == [100]
        assert signed is False

    def test_hex_integer(self):
        regs, signed = encode_value("0x64")
        assert regs == [100]
        assert signed is False

    def test_negative_integer(self):
        regs, signed = encode_value("-1")
        assert regs == [0xFFFF]
        assert signed is True

    def test_long_mode_integer(self):
        regs, signed = encode_value("0x12345678", long_mode=True)
        assert regs == [0x1234, 0x5678]

    def test_float_mode_16bit(self):
        regs, signed = encode_value("1.0", float_mode=True)
        assert regs == [0x3C00]
        assert signed is False

    def test_float_mode_32bit(self):
        regs, signed = encode_value("10.0", long_mode=True, float_mode=True)
        assert regs == [0x4120, 0x0000]

    def test_float_mode_with_hex_raises(self):
        with pytest.raises(EncodingError, match="Hex values are not allowed"):
            encode_value("0x100", float_mode=True)

    def test_invalid_integer_raises(self):
        with pytest.raises(EncodingError, match="must be an integer"):
            encode_value("abc")

    def test_whitespace_stripped(self):
        regs, signed = encode_value("  100  ")
        assert regs == [100]


class TestNormalizeEndian:
    """Tests for normalize_endian function."""

    def test_big(self):
        assert normalize_endian("big") == "big"
        assert normalize_endian("b") == "big"
        assert normalize_endian("BIG") == "big"

    def test_little(self):
        assert normalize_endian("little") == "little"
        assert normalize_endian("l") == "little"

    def test_mid_big(self):
        assert normalize_endian("mid-big") == "mid-big"
        assert normalize_endian("mb") == "mid-big"

    def test_mid_little(self):
        assert normalize_endian("mid-little") == "mid-little"
        assert normalize_endian("ml") == "mid-little"

    def test_all_not_allowed_by_default(self):
        with pytest.raises(EncodingError, match="Unknown endian"):
            normalize_endian("all")

    def test_all_allowed_when_enabled(self):
        assert normalize_endian("all", allow_all=True) == "all"

    def test_unknown_raises(self):
        with pytest.raises(EncodingError, match="Unknown endian"):
            normalize_endian("xyz")
