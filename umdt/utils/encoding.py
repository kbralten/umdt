"""Register/value encoding helpers for Modbus writes.

Centralizes the logic for encoding Python numbers and floats into
16/32-bit register payloads with endian permutations.
"""

import math
import struct
from typing import List, Tuple


class EncodingError(Exception):
    """Raised when encoding a value fails."""
    pass


def encode_int16(
    value: int,
    signed: bool = False,
    endian: str = "big"
) -> List[int]:
    """Encode a 16-bit integer value to a single Modbus register.

    Args:
        value: Integer value to encode
        signed: If True, interpret as signed (-32768 to 32767).
                Note: Negative values automatically enable signed mode.
        endian: Byte order - "big" or "little"

    Returns:
        List with single 16-bit register value

    Raises:
        EncodingError: If value is out of range
    """
    # Auto-detect signed mode for negative values
    if value < 0:
        signed = True
    
    # Validate range
    if signed:
        if value < -0x8000 or value > 0x7FFF:
            raise EncodingError(f"Value {value} out of 16-bit signed range (-32768 to 32767)")
    else:
        if value < 0 or value > 0xFFFF:
            raise EncodingError(f"Value {value} out of 16-bit unsigned range (0 to 65535)")
    
    # Convert to unsigned representation
    if signed and value < 0:
        value_u = value & 0xFFFF
    else:
        value_u = value
    
    # Apply byte order
    b = value_u.to_bytes(2, byteorder="big", signed=False)
    if endian == "little":
        b = b[::-1]
    
    return [int.from_bytes(b, byteorder="big")]


def encode_int32(
    value: int,
    signed: bool = False,
    endian: str = "big"
) -> List[int]:
    """Encode a 32-bit integer value to two Modbus registers.

    Args:
        value: Integer value to encode
        signed: If True, interpret as signed (-2147483648 to 2147483647).
                Note: Negative values automatically enable signed mode.
        endian: Byte/word order - "big", "little", "mid-big", "mid-little"

    Returns:
        List with two 16-bit register values

    Raises:
        EncodingError: If value is out of range
    """
    # Auto-detect signed mode for negative values
    if value < 0:
        signed = True
    
    # Validate range
    if signed:
        if value < -0x80000000 or value > 0x7FFFFFFF:
            raise EncodingError(f"Value {value} out of 32-bit signed range")
    else:
        if value < 0 or value > 0xFFFFFFFF:
            raise EncodingError(f"Value {value} out of 32-bit unsigned range")
    
    # Convert to unsigned representation
    if signed and value < 0:
        value_u = value & 0xFFFFFFFF
    else:
        value_u = value
    
    # Get bytes in big-endian order
    bv = value_u.to_bytes(4, byteorder="big", signed=False)
    
    # Apply endian permutation
    bv = _apply_endian_permutation(bv, endian)
    
    return [
        int.from_bytes(bv[0:2], byteorder="big"),
        int.from_bytes(bv[2:4], byteorder="big"),
    ]


def encode_float16(
    value: float,
    endian: str = "big"
) -> List[int]:
    """Encode a float value to a single Modbus register as IEEE 754 half-precision.

    Args:
        value: Float value to encode
        endian: Byte order - "big" or "little"

    Returns:
        List with single 16-bit register value

    Raises:
        EncodingError: If value cannot be represented as float16
    """
    half = _float_to_half(value)
    
    b = half.to_bytes(2, byteorder="big")
    if endian == "little":
        b = b[::-1]
    
    return [int.from_bytes(b, byteorder="big")]


def encode_float32(
    value: float,
    endian: str = "big"
) -> List[int]:
    """Encode a float value to two Modbus registers as IEEE 754 single-precision.

    Args:
        value: Float value to encode
        endian: Byte/word order - "big", "little", "mid-big", "mid-little"

    Returns:
        List with two 16-bit register values

    Raises:
        EncodingError: If value cannot be represented as float32
    """
    try:
        raw_be = struct.pack("!f", value)
    except (struct.error, OverflowError) as e:
        raise EncodingError(f"Cannot encode {value} as float32: {e}")
    
    # Apply endian permutation
    bv = _apply_endian_permutation(raw_be, endian)
    
    return [
        int.from_bytes(bv[0:2], byteorder="big"),
        int.from_bytes(bv[2:4], byteorder="big"),
    ]


def encode_value(
    value_text: str,
    long_mode: bool = False,
    endian: str = "big",
    float_mode: bool = False,
    signed: bool = False
) -> Tuple[List[int], bool]:
    """Encode a value string into Modbus register list.

    This is the main entry point for encoding user input.

    Args:
        value_text: String representation of the value
        long_mode: If True, encode as 32-bit (two registers)
        endian: Byte/word order
        float_mode: If True, interpret as floating point
        signed: If True for integers, validate as signed range

    Returns:
        Tuple of (register_list, was_signed) where was_signed indicates
        if the value was treated as signed

    Raises:
        EncodingError: If value cannot be encoded
    """
    value_text = value_text.strip()
    is_hex = value_text.lower().startswith("0x")
    
    if float_mode:
        if is_hex:
            raise EncodingError("Hex values are not allowed with float mode")
        try:
            float_val = float(value_text)
        except ValueError:
            raise EncodingError("Value must be a valid float when using float mode")
        
        if long_mode:
            regs = encode_float32(float_val, endian)
        else:
            regs = encode_float16(float_val, endian)
        return (regs, False)
    
    # Integer mode
    try:
        int_val = int(value_text, 0)
    except ValueError:
        raise EncodingError("Value must be an integer or 0xHEX format")
    
    # Auto-detect signed if negative
    if int_val < 0:
        signed = True
    
    if long_mode:
        regs = encode_int32(int_val, signed, endian)
    else:
        regs = encode_int16(int_val, signed, endian)
    
    return (regs, signed)


def _apply_endian_permutation(data: bytes, endian: str) -> bytes:
    """Apply endian permutation to 4-byte data.

    Args:
        data: 4-byte input (big-endian)
        endian: Target byte/word order

    Returns:
        Permuted bytes

    Permutation mappings (input bytes as ABCD):
        - big: ABCD (no change)
        - little: DCBA (full byte reversal)
        - mid-big: CDAB (word swap)
        - mid-little: BADC (byte swap within words)
    """
    if endian == "big":
        return data
    elif endian == "little":
        return data[::-1]
    elif endian == "mid-big":
        return bytes([data[2], data[3], data[0], data[1]])
    elif endian == "mid-little":
        return bytes([data[1], data[0], data[3], data[2]])
    else:
        # Default to big-endian for unknown
        return data


def _float_to_half(f: float) -> int:
    """Convert a Python float to IEEE 754 half-precision (binary16) as an int.

    Args:
        f: Float value to convert

    Returns:
        16-bit integer representing the half-precision float
    """
    if math.isnan(f):
        return 0x7E00  # quiet NaN
    if math.isinf(f):
        return 0x7C00 if f > 0 else 0xFC00
    
    sign = 0
    if f < 0:
        sign = 0x8000
        f = -f
    
    if f == 0.0:
        return sign
    
    # Calculate exponent and mantissa
    exp = int(math.floor(math.log(f, 2)))
    frac = f / (2 ** exp) - 1.0
    exp16 = exp + 15
    
    if exp16 <= 0:
        # Subnormal
        mant16 = int(round(f / (2 ** -24)))
        return sign | mant16
    elif exp16 >= 0x1F:
        # Overflow to infinity
        return sign | 0x7C00
    else:
        # Normal number
        mant16 = int(round(frac * (1 << 10)))
        if mant16 == (1 << 10):
            # Mantissa rounded up to next exponent
            exp16 += 1
            mant16 = 0
            if exp16 >= 0x1F:
                return sign | 0x7C00
        return sign | (exp16 << 10) | (mant16 & 0x3FF)


def normalize_endian(endian_str: str, allow_all: bool = False) -> str:
    """Normalize an endian string to a canonical form.

    Args:
        endian_str: Input endian string (e.g., "b", "big", "l", "little", etc.)
        allow_all: If True, also accept "all" as a valid option

    Returns:
        Canonical endian string ("big", "little", "mid-big", "mid-little", or "all")

    Raises:
        EncodingError: If the endian string is not recognized
    """
    mapping = {
        "b": "big",
        "big": "big",
        "l": "little",
        "little": "little",
        "mb": "mid-big",
        "mid-big": "mid-big",
        "ml": "mid-little",
        "mid-little": "mid-little",
    }
    if allow_all:
        mapping["all"] = "all"
    
    result = mapping.get(endian_str.lower())
    if result is None:
        raise EncodingError(f"Unknown endian format: {endian_str}")
    return result
