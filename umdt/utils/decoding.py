"""Register decoding helpers for Modbus reads.

Centralizes the logic for decoding raw register bytes into display rows
with multiple interpretations (Hex, UInt, Int, Float) across endian permutations.
"""

import struct
from dataclasses import dataclass
from typing import List, Optional, Union
from umdt.utils.ieee754 import from_bytes_to_float16, from_bytes_to_float32


@dataclass
class DecodingRow:
    """A single row of decoded values for one endian format."""
    
    format_name: str  # e.g., "Big", "Little", "Mid-Big", "Mid-Little"
    
    # 16-bit interpretations
    hex16: str = ""
    uint16: Optional[int] = None
    int16: Optional[int] = None
    float16: Optional[float] = None
    float16_str: str = ""  # formatted string or special value like "OVERFLOW"
    
    # 32-bit interpretations (for long/register pairs)
    hex32: str = ""
    uint32: Optional[int] = None
    int32: Optional[int] = None
    float32: Optional[float] = None
    float32_str: str = ""  # formatted string or special value


@dataclass
class DecodingResult:
    """Complete decoding result for a register or register pair."""
    
    rows: List[DecodingRow]
    is_32bit: bool  # True if this is a 32-bit (two-register) decoding
    raw_bytes: bytes  # Original raw bytes for reference


def decode_register16(reg_value: int, include_all_formats: bool = False) -> DecodingResult:
    """Decode a single 16-bit register value.

    Args:
        reg_value: 16-bit register value (0-65535)
        include_all_formats: If True, show Big and Little; if False, only Big

    Returns:
        DecodingResult with decoded values
    """
    # Ensure value is in range
    reg_value = reg_value & 0xFFFF
    raw = reg_value.to_bytes(2, byteorder="big")
    
    formats = [("Big", raw), ("Little", raw[::-1])] if include_all_formats else [("Big", raw)]
    
    rows = []
    for format_name, b in formats:
        uint_val = int.from_bytes(b, byteorder="big", signed=False)
        int_val = uint_val if uint_val < 0x8000 else uint_val - 0x10000
        
        # Float16 interpretation
        try:
            f16_result = from_bytes_to_float16(b)
            if isinstance(f16_result, str):
                f16_val = None
                f16_str = f16_result
            else:
                f16_val = f16_result
                f16_str = f"{f16_result:.6g}"
        except Exception:
            f16_val = None
            f16_str = "—"
        
        rows.append(DecodingRow(
            format_name=format_name,
            hex16=f"0x{b.hex().upper()}",
            uint16=uint_val,
            int16=int_val,
            float16=f16_val,
            float16_str=f16_str,
        ))
    
    return DecodingResult(rows=rows, is_32bit=False, raw_bytes=raw)


def decode_registers32(reg1: int, reg2: int, include_all_formats: bool = True) -> DecodingResult:
    """Decode a pair of 16-bit registers as a 32-bit value.

    Args:
        reg1: First (high) register value
        reg2: Second (low) register value
        include_all_formats: If True, show all 4 permutations

    Returns:
        DecodingResult with decoded values across all endian permutations
    """
    # Ensure values are in range
    reg1 = reg1 & 0xFFFF
    reg2 = reg2 & 0xFFFF
    
    raw = reg1.to_bytes(2, byteorder="big") + reg2.to_bytes(2, byteorder="big")
    
    # Define all permutations
    permutations = _get_32bit_permutations(raw)
    
    if not include_all_formats:
        permutations = [permutations[0]]  # Just Big
    
    rows = []
    for format_name, b in permutations:
        # 32-bit interpretations
        uint32_val = int.from_bytes(b, byteorder="big", signed=False)
        int32_val = uint32_val if uint32_val < 0x80000000 else uint32_val - 0x100000000
        
        # Float32 interpretation
        try:
            f32_result = from_bytes_to_float32(b)
            if isinstance(f32_result, str):
                f32_val = None
                f32_str = f32_result
            else:
                f32_val = f32_result
                f32_str = f"{f32_result:.6g}"
        except Exception:
            f32_val = None
            f32_str = "—"
        
        # Also provide 16-bit interpretation of first register for reference
        first_reg_bytes = b[0:2]
        uint16_val = int.from_bytes(first_reg_bytes, byteorder="big", signed=False)
        int16_val = uint16_val if uint16_val < 0x8000 else uint16_val - 0x10000
        
        try:
            f16_result = from_bytes_to_float16(first_reg_bytes)
            if isinstance(f16_result, str):
                f16_val = None
                f16_str = f16_result
            else:
                f16_val = f16_result
                f16_str = f"{f16_result:.6g}"
        except Exception:
            f16_val = None
            f16_str = "—"
        
        rows.append(DecodingRow(
            format_name=format_name,
            hex16=f"0x{first_reg_bytes.hex().upper()}",
            uint16=uint16_val,
            int16=int16_val,
            float16=f16_val,
            float16_str=f16_str,
            hex32=f"0x{b.hex().upper()}",
            uint32=uint32_val,
            int32=int32_val,
            float32=f32_val,
            float32_str=f32_str,
        ))
    
    return DecodingResult(rows=rows, is_32bit=True, raw_bytes=raw)


def decode_registers(
    registers: List[int],
    long_mode: bool = False,
    include_all_formats: bool = True
) -> DecodingResult:
    """Decode a list of registers.

    This is the main entry point for decoding.

    Args:
        registers: List of 16-bit register values
        long_mode: If True and len(registers) >= 2, decode as 32-bit
        include_all_formats: If True, show all endian permutations

    Returns:
        DecodingResult with decoded values
    """
    if not registers:
        return DecodingResult(rows=[], is_32bit=False, raw_bytes=b'')
    
    if long_mode and len(registers) >= 2:
        return decode_registers32(registers[0], registers[1], include_all_formats)
    else:
        return decode_register16(registers[0], include_all_formats)


def decode_to_table_dict(result: DecodingResult) -> List[dict]:
    """Convert a DecodingResult to a list of dicts suitable for table display.

    Each dict has keys: Format, Hex, UInt16, Int16, Float16, Hex32, UInt32, Int32, Float32

    Args:
        result: DecodingResult to convert

    Returns:
        List of dicts for table display
    """
    table_rows = []
    for row in result.rows:
        table_rows.append({
            'Format': row.format_name,
            'Hex': row.hex16,
            'UInt16': str(row.uint16) if row.uint16 is not None else "",
            'Int16': str(row.int16) if row.int16 is not None else "",
            'Float16': row.float16_str,
            'Hex32': row.hex32,
            'UInt32': str(row.uint32) if row.uint32 is not None else "",
            'Int32': str(row.int32) if row.int32 is not None else "",
            'Float32': row.float32_str,
        })
    return table_rows


def format_permutations_32(registers: List[int]) -> dict:
    """Return structured info for the four common 32-bit orderings.

    This is a compatibility wrapper for existing CLI code that uses
    the _format_permutations function.

    Args:
        registers: List of two 16-bit register values

    Returns:
        Dict mapping label -> dict with keys: `bytes`, `hex`, `uint32`, `float`
    """
    if len(registers) < 2:
        return {}
    
    raw = registers[0].to_bytes(2, byteorder="big") + registers[1].to_bytes(2, byteorder="big")
    permutations = _get_32bit_permutations(raw)
    
    out = {}
    for label, bv in permutations:
        try:
            f32 = from_bytes_to_float32(bv)
        except Exception:
            f32 = None
        
        u32 = int.from_bytes(bv, byteorder='big', signed=False)
        out[label] = {
            "bytes": bv,
            "hex": bv.hex().upper(),
            "uint32": u32,
            "float": f32,
        }
    
    return out


def float_permutations_from_regs(registers: List[int]) -> dict:
    """Get float32 values for all four endian permutations.

    This is a compatibility wrapper for existing CLI code.

    Args:
        registers: List of two 16-bit register values

    Returns:
        Dict mapping endian label to float32 value
    """
    perms = format_permutations_32(registers)
    return {k: v['float'] for k, v in perms.items()}


def _get_32bit_permutations(raw: bytes) -> List[tuple]:
    """Get all four 32-bit endian permutations.

    Args:
        raw: 4-byte input (big-endian ABCD)

    Returns:
        List of (name, bytes) tuples for each permutation
    """
    return [
        ("Big", raw),
        ("Little", raw[::-1]),
        ("Mid-Big", bytes([raw[2], raw[3], raw[0], raw[1]])),
        ("Mid-Little", bytes([raw[1], raw[0], raw[3], raw[2]])),
    ]
