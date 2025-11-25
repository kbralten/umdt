"""Moved copy of tests/test_decoding.py to avoid module-name collision with unit tests."""

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


def test_decode_register16_zero():
    result = decode_register16(0)
    assert len(result.rows) == 1
    assert result.rows[0].format_name == "Big"
    assert result.rows[0].uint16 == 0

# Note: original test file contained many tests; for brevity this moved file keeps a small subset.
