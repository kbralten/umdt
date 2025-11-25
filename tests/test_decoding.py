"""Tests for umdt.utils.decoding module."""

import pytest
from umdt.utils.decoding import (
    decode_register16,
    decode_registers32,
    decode_registers,
    decode_to_table_dict,
    format_permutations_32,
    float_permutations_from_regs,
    DecodingRow,
    DecodingResult,
)


class TestDecodeRegister16:
    """Tests for decode_register16 function."""

    def test_zero(self):
        result = decode_register16(0)
        assert len(result.rows) == 1
        assert result.rows[0].format_name == "Big"
        assert result.rows[0].uint16 == 0
        assert result.rows[0].int16 == 0

    def test_positive_value(self):
        result = decode_register16(0x1234)
        row = result.rows[0]
        assert row.uint16 == 0x1234
        assert row.hex16 == "0x1234"

    def test_max_unsigned(self):
        result = decode_register16(0xFFFF)
        row = result.rows[0]
        assert row.uint16 == 65535
        assert row.int16 == -1  # Signed interpretation

    def test_include_all_formats(self):
        result = decode_register16(0x1234, include_all_formats=True)
        assert len(result.rows) == 2
        assert result.rows[0].format_name == "Big"
        assert result.rows[1].format_name == "Little"
        # Little endian should reverse bytes
        assert result.rows[1].hex16 == "0x3412"

    def test_float16_decoding(self):
        # 0x3C00 is 1.0 in float16
        result = decode_register16(0x3C00)
        row = result.rows[0]
        assert row.float16 is not None
        assert abs(row.float16 - 1.0) < 0.01

    def test_is_not_32bit(self):
        result = decode_register16(100)
        assert result.is_32bit is False


class TestDecodeRegisters32:
    """Tests for decode_registers32 function."""

    def test_zero(self):
        result = decode_registers32(0, 0)
        assert len(result.rows) == 4  # All four formats
        big_row = result.rows[0]
        assert big_row.format_name == "Big"
        assert big_row.uint32 == 0
        assert big_row.int32 == 0

    def test_positive_value(self):
        # 0x41200000 is 10.0 in float32, split as [0x4120, 0x0000]
        result = decode_registers32(0x4120, 0x0000)
        big_row = result.rows[0]
        assert big_row.hex32 == "0x41200000"
        assert big_row.float32 is not None
        assert abs(big_row.float32 - 10.0) < 0.001

    def test_all_four_permutations(self):
        result = decode_registers32(0x1234, 0x5678)
        assert len(result.rows) == 4
        names = [r.format_name for r in result.rows]
        assert "Big" in names
        assert "Little" in names
        assert "Mid-Big" in names
        assert "Mid-Little" in names

    def test_big_endian(self):
        # Input: 0x12345678 as [0x1234, 0x5678]
        result = decode_registers32(0x1234, 0x5678)
        big_row = [r for r in result.rows if r.format_name == "Big"][0]
        assert big_row.hex32 == "0x12345678"
        assert big_row.uint32 == 0x12345678

    def test_little_endian(self):
        # Input: 0x12345678, Little reverses to 0x78563412
        result = decode_registers32(0x1234, 0x5678)
        little_row = [r for r in result.rows if r.format_name == "Little"][0]
        assert little_row.hex32 == "0x78563412"

    def test_mid_big_endian(self):
        # Input: 0x12345678, Mid-Big swaps words to 0x56781234
        result = decode_registers32(0x1234, 0x5678)
        mid_big_row = [r for r in result.rows if r.format_name == "Mid-Big"][0]
        assert mid_big_row.hex32 == "0x56781234"

    def test_mid_little_endian(self):
        # Input: 0x12345678, Mid-Little swaps bytes within words to 0x34127856
        result = decode_registers32(0x1234, 0x5678)
        mid_little_row = [r for r in result.rows if r.format_name == "Mid-Little"][0]
        assert mid_little_row.hex32 == "0x34127856"

    def test_is_32bit(self):
        result = decode_registers32(0, 0)
        assert result.is_32bit is True


class TestDecodeRegisters:
    """Tests for the main decode_registers entry point."""

    def test_empty_list(self):
        result = decode_registers([])
        assert len(result.rows) == 0
        assert result.is_32bit is False

    def test_single_register(self):
        result = decode_registers([0x1234])
        assert result.is_32bit is False
        assert result.rows[0].uint16 == 0x1234

    def test_two_registers_no_long_mode(self):
        result = decode_registers([0x1234, 0x5678], long_mode=False)
        # Should only decode first register as 16-bit
        assert result.is_32bit is False
        assert result.rows[0].uint16 == 0x1234

    def test_two_registers_long_mode(self):
        result = decode_registers([0x1234, 0x5678], long_mode=True)
        assert result.is_32bit is True
        big_row = result.rows[0]
        assert big_row.hex32 == "0x12345678"

    def test_include_all_formats(self):
        result = decode_registers([100], include_all_formats=True)
        assert len(result.rows) == 2  # Big and Little


class TestDecodeToTableDict:
    """Tests for decode_to_table_dict function."""

    def test_16bit_conversion(self):
        result = decode_register16(0x1234, include_all_formats=True)
        table = decode_to_table_dict(result)
        
        assert len(table) == 2
        assert table[0]['Format'] == "Big"
        assert table[0]['Hex'] == "0x1234"
        assert table[0]['UInt16'] == "4660"

    def test_32bit_conversion(self):
        result = decode_registers32(0x4120, 0x0000)
        table = decode_to_table_dict(result)
        
        assert len(table) == 4
        big_row = table[0]
        assert big_row['Format'] == "Big"
        assert big_row['Hex32'] == "0x41200000"
        assert "10" in big_row['Float32']  # Should be approximately 10.0


class TestFormatPermutations32:
    """Tests for format_permutations_32 compatibility wrapper."""

    def test_basic_usage(self):
        result = format_permutations_32([0x4120, 0x0000])
        
        assert "Big" in result
        assert "Little" in result
        assert "Mid-Big" in result
        assert "Mid-Little" in result
        
        assert result["Big"]["hex"] == "41200000"
        assert abs(result["Big"]["float"] - 10.0) < 0.001

    def test_empty_list(self):
        result = format_permutations_32([])
        assert result == {}

    def test_single_register(self):
        result = format_permutations_32([0x1234])
        assert result == {}


class TestFloatPermutationsFromRegs:
    """Tests for float_permutations_from_regs compatibility wrapper."""

    def test_basic_usage(self):
        # 10.0 in float32 is 0x41200000
        result = float_permutations_from_regs([0x4120, 0x0000])
        
        assert "Big" in result
        assert abs(result["Big"] - 10.0) < 0.001

    def test_all_permutations_present(self):
        result = float_permutations_from_regs([0x1234, 0x5678])
        
        assert len(result) == 4
        assert "Big" in result
        assert "Little" in result
        assert "Mid-Big" in result
        assert "Mid-Little" in result
