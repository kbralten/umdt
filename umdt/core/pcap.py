"""PCAP (Packet Capture) writer for forensic logging.

This module provides a reusable PCAP file writer that can be used by any UMDT tool
to log raw traffic for later analysis in Wireshark or similar tools.

The PCAP format used is standard libpcap with linktype DLT_USER0 (147), which is
reserved for user-defined protocols. This allows Modbus frames to be logged as-is
without requiring fake Ethernet/IP headers.

Usage:
    async with PcapWriter("capture.pcap") as pcap:
        pcap.write_packet(raw_bytes, direction=Direction.INBOUND)

The writer is thread-safe and async-safe for concurrent use.
"""
from __future__ import annotations

import asyncio
import struct
import time
from contextlib import asynccontextmanager, contextmanager
from enum import IntEnum
from pathlib import Path
from typing import BinaryIO, Optional, Union


# PCAP file format constants (little-endian, microsecond resolution)
PCAP_MAGIC_NUMBER = 0xA1B2C3D4
PCAP_VERSION_MAJOR = 2
PCAP_VERSION_MINOR = 4
PCAP_THISZONE = 0  # GMT
PCAP_SIGFIGS = 0   # Accuracy of timestamps
PCAP_SNAPLEN = 65535  # Max packet length

# Link-layer header types
# DLT_USER0 (147) is reserved for private use - perfect for raw Modbus frames
# See https://www.tcpdump.org/linktypes.html
DLT_USER0 = 147
DLT_RAW = 101  # Raw IP (alternative, but we use USER0)


class Direction(IntEnum):
    """Packet direction for metadata (stored in first byte of USER0 payload)."""
    UNKNOWN = 0
    INBOUND = 1   # Request from master / upstream
    OUTBOUND = 2  # Response from slave / downstream


class PcapWriter:
    """Write packets to a PCAP file for analysis in Wireshark.

    The writer uses DLT_USER0 linktype and prepends a 4-byte metadata header:
      - Byte 0: Direction (0=unknown, 1=inbound, 2=outbound)
      - Byte 1: Protocol hint (0=unknown, 1=Modbus RTU, 2=Modbus TCP)
      - Bytes 2-3: Reserved (zero)

    This allows Wireshark dissectors or custom scripts to interpret the data.

    Thread-safety: All write operations are protected by an asyncio lock.
    """

    # Protocol hints for the metadata header
    PROTO_UNKNOWN = 0
    PROTO_MODBUS_RTU = 1
    PROTO_MODBUS_TCP = 2

    def __init__(
        self,
        filepath: Union[str, Path],
        snaplen: int = PCAP_SNAPLEN,
        linktype: int = DLT_USER0,
    ):
        """Initialize the PCAP writer.

        Args:
            filepath: Path to the output .pcap file
            snaplen: Maximum packet length to capture
            linktype: Link-layer type (default: DLT_USER0 for custom protocols)
        """
        self.filepath = Path(filepath)
        self.snaplen = snaplen
        self.linktype = linktype
        self._file: Optional[BinaryIO] = None
        self._lock = asyncio.Lock()
        self._packet_count = 0
        self._bytes_written = 0

    def open(self) -> None:
        """Open the PCAP file and write the global header."""
        self._file = open(self.filepath, "wb")
        self._write_global_header()

    def close(self) -> None:
        """Close the PCAP file."""
        if self._file:
            self._file.flush()
            self._file.close()
            self._file = None

    async def aclose(self) -> None:
        """Async close with lock protection."""
        async with self._lock:
            self.close()

    def _write_global_header(self) -> None:
        """Write the PCAP global header (24 bytes)."""
        if not self._file:
            return

        header = struct.pack(
            "<IHHIIII",
            PCAP_MAGIC_NUMBER,
            PCAP_VERSION_MAJOR,
            PCAP_VERSION_MINOR,
            PCAP_THISZONE,
            PCAP_SIGFIGS,
            self.snaplen,
            self.linktype,
        )
        self._file.write(header)
        self._bytes_written += len(header)

    def write_packet(
        self,
        data: bytes,
        direction: Direction = Direction.UNKNOWN,
        protocol: int = PROTO_UNKNOWN,
        timestamp: Optional[float] = None,
    ) -> None:
        """Write a packet to the PCAP file (synchronous).

        Args:
            data: Raw packet bytes
            direction: Packet direction (inbound/outbound)
            protocol: Protocol hint (RTU/TCP)
            timestamp: Unix timestamp (defaults to current time)
        """
        if not self._file:
            raise RuntimeError("PCAP file not open")

        if timestamp is None:
            timestamp = time.time()

        # Build metadata header (4 bytes)
        metadata = struct.pack("BBBB", direction, protocol, 0, 0)
        full_data = metadata + data

        # Truncate if necessary
        captured_len = min(len(full_data), self.snaplen)
        original_len = len(full_data)

        # Convert timestamp to seconds and microseconds
        ts_sec = int(timestamp)
        ts_usec = int((timestamp - ts_sec) * 1_000_000)

        # Write packet header (16 bytes)
        pkt_header = struct.pack(
            "<IIII",
            ts_sec,
            ts_usec,
            captured_len,
            original_len,
        )
        self._file.write(pkt_header)
        self._file.write(full_data[:captured_len])

        self._packet_count += 1
        self._bytes_written += 16 + captured_len

    async def write_packet_async(
        self,
        data: bytes,
        direction: Direction = Direction.UNKNOWN,
        protocol: int = PROTO_UNKNOWN,
        timestamp: Optional[float] = None,
    ) -> None:
        """Write a packet to the PCAP file (async, thread-safe).

        Args:
            data: Raw packet bytes
            direction: Packet direction (inbound/outbound)
            protocol: Protocol hint (RTU/TCP)
            timestamp: Unix timestamp (defaults to current time)
        """
        async with self._lock:
            self.write_packet(data, direction, protocol, timestamp)

    def flush(self) -> None:
        """Flush pending writes to disk."""
        if self._file:
            self._file.flush()

    async def flush_async(self) -> None:
        """Async flush with lock protection."""
        async with self._lock:
            self.flush()

    @property
    def packet_count(self) -> int:
        """Number of packets written."""
        return self._packet_count

    @property
    def bytes_written(self) -> int:
        """Total bytes written to file."""
        return self._bytes_written

    # Context managers for convenient usage

    def __enter__(self) -> "PcapWriter":
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    async def __aenter__(self) -> "PcapWriter":
        self.open()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.aclose()


@contextmanager
def open_pcap(filepath: Union[str, Path], **kwargs):
    """Context manager for synchronous PCAP writing.

    Usage:
        with open_pcap("capture.pcap") as pcap:
            pcap.write_packet(data)
    """
    writer = PcapWriter(filepath, **kwargs)
    writer.open()
    try:
        yield writer
    finally:
        writer.close()


@asynccontextmanager
async def open_pcap_async(filepath: Union[str, Path], **kwargs):
    """Async context manager for PCAP writing.

    Usage:
        async with open_pcap_async("capture.pcap") as pcap:
            await pcap.write_packet_async(data)
    """
    writer = PcapWriter(filepath, **kwargs)
    writer.open()
    try:
        yield writer
    finally:
        await writer.aclose()
