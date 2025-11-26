"""Downstream client - connects to Modbus Slaves (PLC/Sensor).

Supports both TCP and RTU (serial) modes as a Modbus Master that forwards
requests from the bridge pipeline to the actual slave device.
"""
from __future__ import annotations

import asyncio
import logging
import struct
from typing import Optional

from .protocol import FrameType, ModbusFrameParser

logger = logging.getLogger("umdt.bridge.downstream")


class DownstreamClient:
    """Modbus client connecting to downstream slave devices.

    For TCP mode: Connects to a TCP server (e.g., Modbus TCP device).
    For RTU mode: Opens a serial port and sends RTU frames.
    """

    def __init__(
        self,
        frame_type: FrameType = FrameType.RTU,
        host: Optional[str] = None,
        port: int = 502,
        serial_port: Optional[str] = None,
        baudrate: int = 9600,
        timeout: float = 2.0,
    ):
        self.frame_type = frame_type
        self.host = host
        self.port = port
        self.serial_port = serial_port
        self.baudrate = baudrate
        self.timeout = timeout

        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._connected = False
        self._lock = asyncio.Lock()  # Serialize access to the connection

    async def connect(self) -> None:
        """Connect to the downstream device."""
        if self._connected:
            return

        if self.frame_type == FrameType.TCP:
            await self._connect_tcp()
        else:
            await self._connect_serial()

        self._connected = True

    async def disconnect(self) -> None:
        """Disconnect from the downstream device."""
        self._connected = False

        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
            self._reader = None

        logger.info("Downstream client disconnected")

    async def send_request(self, frame: bytes) -> Optional[bytes]:
        """Send a request frame and wait for response.

        This method is thread-safe and serializes access to the connection.

        Returns:
            Response frame, or None on timeout/error.
        """
        async with self._lock:
            if not self._connected:
                await self.connect()

            try:
                return await self._send_and_receive(frame)
            except Exception as e:
                logger.error("Downstream communication error: %s", e)
                # Try to reconnect on next request
                self._connected = False
                return None

    # --- TCP Mode ---

    async def _connect_tcp(self) -> None:
        """Connect to TCP downstream device."""
        if not self.host:
            raise ValueError("Host not configured for TCP mode")

        logger.info("Connecting to downstream TCP %s:%d", self.host, self.port)
        self._reader, self._writer = await asyncio.open_connection(
            self.host,
            self.port,
        )
        logger.info("Connected to downstream TCP %s:%d", self.host, self.port)

    async def _send_and_receive_tcp(self, frame: bytes) -> Optional[bytes]:
        """Send TCP frame and receive response."""
        if not self._writer or not self._reader:
            raise RuntimeError("Not connected")

        logger.debug("Sending to downstream TCP: %s", frame.hex().upper())

        self._writer.write(frame)
        await self._writer.drain()

        # Read MBAP header first to determine frame length
        try:
            header = await asyncio.wait_for(
                self._reader.readexactly(7),
                timeout=self.timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("Timeout waiting for downstream TCP response header")
            return None
        except asyncio.IncompleteReadError:
            logger.warning("Downstream TCP connection closed")
            self._connected = False
            return None

        # Parse length from MBAP header
        length = struct.unpack(">H", header[4:6])[0]
        pdu_length = length - 1  # Subtract unit_id byte (already in header)

        # Read PDU
        try:
            pdu = await asyncio.wait_for(
                self._reader.readexactly(pdu_length),
                timeout=self.timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("Timeout waiting for downstream TCP response PDU")
            return None
        except asyncio.IncompleteReadError:
            logger.warning("Downstream TCP connection closed during PDU read")
            self._connected = False
            return None

        response = header + pdu
        logger.debug("Received from downstream TCP: %s", response.hex().upper())
        return response

    # --- RTU (Serial) Mode ---

    async def _connect_serial(self) -> None:
        """Connect to serial downstream device."""
        if not self.serial_port:
            raise ValueError("Serial port not configured for RTU mode")

        try:
            import serial_asyncio
        except ImportError:
            raise ImportError("serial_asyncio required for RTU mode: pip install pyserial-asyncio")

        logger.info(
            "Opening downstream serial %s @ %d baud",
            self.serial_port,
            self.baudrate,
        )

        self._reader, self._writer = await serial_asyncio.open_serial_connection(
            url=self.serial_port,
            baudrate=self.baudrate,
        )
        logger.info("Downstream serial port opened")

    async def _send_and_receive_rtu(self, frame: bytes) -> Optional[bytes]:
        """Send RTU frame and receive response."""
        if not self._writer or not self._reader:
            raise RuntimeError("Not connected")

        logger.debug("Sending to downstream RTU: %s", frame.hex().upper())

        # Clear any pending data in the buffer
        try:
            while True:
                data = await asyncio.wait_for(self._reader.read(256), timeout=0.01)
                if not data:
                    break
        except asyncio.TimeoutError:
            pass

        # Send frame
        self._writer.write(frame)
        await self._writer.drain()

        # RTU inter-frame gap
        inter_frame_gap = max(0.004, (11 * 3.5) / self.baudrate)

        # Read response with inter-character timeout
        buffer = b""
        last_byte_time = 0.0

        import time
        start = time.monotonic()

        while (time.monotonic() - start) < self.timeout:
            try:
                data = await asyncio.wait_for(self._reader.read(256), timeout=0.05)
                if data:
                    now = time.monotonic()

                    # Check for inter-frame gap (end of frame)
                    if buffer and (now - last_byte_time) > inter_frame_gap:
                        break

                    buffer += data
                    last_byte_time = now

            except asyncio.TimeoutError:
                if buffer:
                    # No more data coming, frame complete
                    break
                continue

        if not buffer:
            logger.warning("Timeout waiting for downstream RTU response")
            return None

        # Verify CRC
        if not ModbusFrameParser.verify_crc(buffer):
            logger.warning("Invalid CRC in downstream RTU response: %s", buffer.hex().upper())
            return None

        logger.debug("Received from downstream RTU: %s", buffer.hex().upper())
        return buffer

    async def _send_and_receive(self, frame: bytes) -> Optional[bytes]:
        """Send frame and receive response based on mode."""
        if self.frame_type == FrameType.TCP:
            return await self._send_and_receive_tcp(frame)
        else:
            return await self._send_and_receive_rtu(frame)

    # --- Properties ---

    @property
    def is_connected(self) -> bool:
        return self._connected
