from typing import Iterable


def validate_uint16(value: int) -> int:
    if not isinstance(value, int):
        raise ValueError("uint16 must be an integer")
    if value < 0 or value > 0xFFFF:
        raise ValueError("uint16 out of range (0..65535)")
    return value


def validate_registers(registers: Iterable[int]):
    regs = list(registers)
    if not regs:
        raise ValueError("registers must be non-empty")
    for r in regs:
        validate_uint16(r)
    return regs
