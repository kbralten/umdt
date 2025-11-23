import math
from umdt.utils.ieee754 import (
    from_bytes_to_float32,
    from_bytes_to_float64,
    registers_to_float32,
)


def test_float32_normal():
    b = (0x41, 0x20, 0x00, 0x00)  # 10.0 in >f
    val = from_bytes_to_float32(bytes(b))
    assert isinstance(val, float)
    assert abs(val - 10.0) < 1e-6


def test_float32_nan():
    # quiet NaN pattern
    b = bytes([0x7F, 0xC0, 0x00, 0x01])
    val = from_bytes_to_float32(b)
    assert val == "SENSOR FAULT"


def test_float32_inf():
    b = bytes([0x7F, 0x80, 0x00, 0x00])
    val = from_bytes_to_float32(b)
    assert val == "OVERFLOW"


def test_registers_to_float32():
    # registers for 10.0 (0x41200000) -> [0x4120, 0x0000]
    regs = [0x4120, 0x0000]
    val = registers_to_float32(regs, 0)
    assert isinstance(val, float)
    assert abs(val - 10.0) < 1e-6
