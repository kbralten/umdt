"""Modbus protocol utilities for frame parsing and conversion.

Handles MBAP (TCP) and RTU frame formats, CRC calculation, and conversion
between the two protocols.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional, Tuple


class FrameType(Enum):
    """Type of Modbus frame encoding."""
    RTU = auto()   # RTU with CRC16
    TCP = auto()   # TCP with MBAP header


@dataclass
class MBAPHeader:
    """Modbus TCP Application Protocol header."""
    transaction_id: int   # 2 bytes - client-assigned request identifier
    protocol_id: int      # 2 bytes - always 0 for Modbus
    length: int           # 2 bytes - number of following bytes (unit_id + pdu)
    unit_id: int          # 1 byte - slave address

    def to_bytes(self) -> bytes:
        return struct.pack(
            ">HHHB",
            self.transaction_id,
            self.protocol_id,
            self.length,
            self.unit_id,
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> "MBAPHeader":
        if len(data) < 7:
            raise ValueError("MBAP header requires at least 7 bytes")
        trans_id, proto_id, length, unit_id = struct.unpack(">HHHB", data[:7])
        return cls(
            transaction_id=trans_id,
            protocol_id=proto_id,
            length=length,
            unit_id=unit_id,
        )


@dataclass
class ModbusPDU:
    """Modbus Protocol Data Unit - the core request/response."""
    function_code: int
    data: bytes

    def to_bytes(self) -> bytes:
        return bytes([self.function_code]) + self.data

    @classmethod
    def from_bytes(cls, data: bytes) -> "ModbusPDU":
        if len(data) < 1:
            raise ValueError("PDU requires at least 1 byte (function code)")
        return cls(function_code=data[0], data=data[1:])


class ModbusFrameParser:
    """Parse and convert between Modbus RTU and TCP frame formats."""

    @staticmethod
    def compute_crc16(data: bytes) -> int:
        """Compute Modbus CRC16 for RTU frames."""
        crc = 0xFFFF
        for byte in data:
            crc ^= byte
            for _ in range(8):
                if crc & 0x0001:
                    crc = (crc >> 1) ^ 0xA001
                else:
                    crc >>= 1
        return crc

    @staticmethod
    def verify_crc(frame: bytes) -> bool:
        """Verify CRC of an RTU frame."""
        if len(frame) < 4:
            return False
        received_crc = struct.unpack("<H", frame[-2:])[0]
        computed_crc = ModbusFrameParser.compute_crc16(frame[:-2])
        return received_crc == computed_crc

    @staticmethod
    def parse_tcp_frame(data: bytes) -> Tuple[MBAPHeader, ModbusPDU]:
        """Parse a complete Modbus TCP frame into header and PDU."""
        if len(data) < 8:
            raise ValueError("TCP frame too short")
        header = MBAPHeader.from_bytes(data[:7])
        pdu = ModbusPDU.from_bytes(data[7:])
        return header, pdu

    @staticmethod
    def parse_rtu_frame(data: bytes, verify_crc: bool = True) -> Tuple[int, ModbusPDU]:
        """Parse a complete Modbus RTU frame into unit_id and PDU.

        Returns:
            Tuple of (unit_id, pdu)
        """
        if len(data) < 4:
            raise ValueError("RTU frame too short")
        if verify_crc and not ModbusFrameParser.verify_crc(data):
            raise ValueError("RTU frame CRC mismatch")
        unit_id = data[0]
        pdu = ModbusPDU.from_bytes(data[1:-2])
        return unit_id, pdu

    @staticmethod
    def build_tcp_frame(
        unit_id: int,
        pdu: ModbusPDU,
        transaction_id: int = 0,
    ) -> bytes:
        """Build a Modbus TCP frame from components."""
        pdu_bytes = pdu.to_bytes()
        length = 1 + len(pdu_bytes)  # unit_id + pdu
        header = MBAPHeader(
            transaction_id=transaction_id,
            protocol_id=0,
            length=length,
            unit_id=unit_id,
        )
        return header.to_bytes() + pdu_bytes

    @staticmethod
    def build_rtu_frame(unit_id: int, pdu: ModbusPDU) -> bytes:
        """Build a Modbus RTU frame with CRC."""
        frame = bytes([unit_id]) + pdu.to_bytes()
        crc = ModbusFrameParser.compute_crc16(frame)
        return frame + struct.pack("<H", crc)

    @staticmethod
    def tcp_to_rtu(tcp_frame: bytes) -> bytes:
        """Convert a TCP frame to RTU format (strip MBAP, add CRC)."""
        header, pdu = ModbusFrameParser.parse_tcp_frame(tcp_frame)
        return ModbusFrameParser.build_rtu_frame(header.unit_id, pdu)

    @staticmethod
    def rtu_to_tcp(rtu_frame: bytes, transaction_id: int = 0) -> bytes:
        """Convert an RTU frame to TCP format (strip CRC, add MBAP)."""
        unit_id, pdu = ModbusFrameParser.parse_rtu_frame(rtu_frame)
        return ModbusFrameParser.build_tcp_frame(unit_id, pdu, transaction_id)

    @staticmethod
    def extract_mbap_transaction_id(tcp_frame: bytes) -> int:
        """Extract transaction ID from TCP frame for response matching."""
        if len(tcp_frame) < 2:
            return 0
        return struct.unpack(">H", tcp_frame[:2])[0]

    @staticmethod
    def get_expected_response_length(pdu: ModbusPDU, frame_type: FrameType) -> Optional[int]:
        """Estimate expected response length based on function code.

        Returns None if length cannot be determined (variable-length response).
        """
        fc = pdu.function_code

        # Base overhead
        if frame_type == FrameType.TCP:
            overhead = 7 + 1  # MBAP (7) + function code (1)
        else:
            overhead = 1 + 1 + 2  # unit_id (1) + function code (1) + CRC (2)

        # Read functions: response length depends on count requested
        if fc in (0x01, 0x02):  # Read Coils / Discrete Inputs
            if len(pdu.data) >= 4:
                count = struct.unpack(">H", pdu.data[2:4])[0]
                byte_count = (count + 7) // 8
                return overhead + 1 + byte_count  # +1 for byte count field
        elif fc in (0x03, 0x04):  # Read Holding/Input Registers
            if len(pdu.data) >= 4:
                count = struct.unpack(">H", pdu.data[2:4])[0]
                return overhead + 1 + count * 2  # +1 for byte count field
        elif fc in (0x05, 0x06):  # Write Single Coil/Register
            return overhead + 4  # Echo of address + value
        elif fc in (0x0F, 0x10):  # Write Multiple Coils/Registers
            return overhead + 4  # Echo of address + count

        return None
