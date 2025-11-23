from typing import List
import struct


try:
    from pymodbus.payload import BinaryPayloadBuilder
    from pymodbus.constants import Endian
except Exception:
    BinaryPayloadBuilder = None
    Endian = None


class CommandBuilder:
    """Convenience wrapper around pymodbus BinaryPayloadBuilder.

    If `pymodbus` is available it is used; otherwise a lightweight
    fallback implementation is provided so the builder can still
    produce register lists and bytes.
    """
    def __init__(self):
        if BinaryPayloadBuilder is not None:
            # Use big-endian by default (network / Modbus big-endian word order)
            self._impl = BinaryPayloadBuilder(byteorder=Endian.Big, wordorder=Endian.Big)
            self._use_pymodbus = True
        else:
            self._regs: List[int] = []
            self._use_pymodbus = False

    def add_uint16(self, value: int):
        if self._use_pymodbus:
            self._impl.add_16bit_uint(int(value))
        else:
            self._regs.append(int(value) & 0xFFFF)
        return self

    def add_int16(self, value: int):
        if self._use_pymodbus:
            self._impl.add_16bit_int(int(value))
        else:
            self._regs.append(int(value) & 0xFFFF)
        return self

    def add_float32_be(self, value: float):
        if self._use_pymodbus:
            self._impl.add_32bit_float(float(value))
        else:
            b = struct.pack('>f', float(value))
            self._regs.append((b[0] << 8) | b[1])
            self._regs.append((b[2] << 8) | b[3])
        return self

    def add_float64_be(self, value: float):
        if self._use_pymodbus:
            self._impl.add_64bit_float(float(value))
        else:
            b = struct.pack('>d', float(value))
            for i in range(0, 8, 2):
                self._regs.append((b[i] << 8) | b[i + 1])
        return self

    def get_registers(self) -> List[int]:
        """Return a list of 16-bit register integers."""
        if self._use_pymodbus:
            return list(self._impl.to_registers())
        return list(self._regs)

    def to_bytes(self) -> bytes:
        regs = self.get_registers()
        out = bytearray()
        for r in regs:
            out.append((r >> 8) & 0xFF)
            out.append(r & 0xFF)
        return bytes(out)
