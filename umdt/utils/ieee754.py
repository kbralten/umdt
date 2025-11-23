import struct
import math
from typing import Sequence, Union


def from_bytes_to_float32(b: bytes) -> Union[float, str]:
    """Interpret 4 bytes as a big-endian IEEE-754 float32.

    Returns the numeric float, or the strings "SENSOR FAULT" for NaN
    and "OVERFLOW" for infinities.
    """
    if len(b) != 4:
        raise ValueError("float32 requires exactly 4 bytes")
    val = struct.unpack('>f', b)[0]
    if math.isnan(val):
        return "SENSOR FAULT"
    if math.isinf(val):
        return "OVERFLOW"
    return val


def from_bytes_to_float64(b: bytes) -> Union[float, str]:
    """Interpret 8 bytes as a big-endian IEEE-754 float64.

    Returns the numeric float, or the strings "SENSOR FAULT" for NaN
    and "OVERFLOW" for infinities.
    """
    if len(b) != 8:
        raise ValueError("float64 requires exactly 8 bytes")
    val = struct.unpack('>d', b)[0]
    if math.isnan(val):
        return "SENSOR FAULT"
    if math.isinf(val):
        return "OVERFLOW"
    return val


def from_bytes_to_float16(b: bytes) -> Union[float, str]:
    """Interpret 2 bytes as a big-endian IEEE-754 float16 (binary16).

    Returns the numeric float, or the strings "SENSOR FAULT" for NaN
    and "OVERFLOW" for infinities.
    """
    if len(b) != 2:
        raise ValueError("float16 requires exactly 2 bytes")
    # Decode IEEE 754 binary16
    h = int.from_bytes(b, byteorder='big', signed=False)
    sign = (h >> 15) & 0x1
    exp = (h >> 10) & 0x1F
    frac = h & 0x3FF

    if exp == 0:
        if frac == 0:
            val = -0.0 if sign else 0.0
            return val
        # subnormal
        val = ((-1) ** sign) * (frac / 1024.0) * (2 ** (-14))
        return val
    if exp == 0x1F:
        if frac == 0:
            return "OVERFLOW"
        return "SENSOR FAULT"

    # normalised
    mant = 1.0 + (frac / 1024.0)
    val = ((-1) ** sign) * mant * (2 ** (exp - 15))
    return val


def registers_to_bytes_be(registers: Sequence[int], start: int, count: int) -> bytes:
    """Convert `count` 16-bit registers starting at `start` into big-endian bytes.

    Each register is expected as an integer 0..0xFFFF.
    """
    end = start + count
    if end > len(registers):
        raise IndexError("Not enough registers")
    out = bytearray()
    for r in registers[start:end]:
        if not (0 <= r <= 0xFFFF):
            raise ValueError("register values must be 0..0xFFFF")
        out.append((r >> 8) & 0xFF)
        out.append(r & 0xFF)
    return bytes(out)


def registers_to_float32(registers: Sequence[int], start: int = 0) -> Union[float, str]:
    """Read two 16-bit registers at `start` as a big-endian float32 (">f")."""
    b = registers_to_bytes_be(registers, start, 2)
    return from_bytes_to_float32(b)


def registers_to_float64(registers: Sequence[int], start: int = 0) -> Union[float, str]:
    """Read four 16-bit registers at `start` as a big-endian float64 (">d")."""
    b = registers_to_bytes_be(registers, start, 4)
    return from_bytes_to_float64(b)
