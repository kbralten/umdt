"""Upstream server - accepts connections from Modbus Masters (SCADA/HMI).

Supports both TCP and RTU (serial) modes as a Modbus Slave that relays
requests through the bridge pipeline to the downstream device.
"""
from __future__ import annotations

import asyncio
import logging
import struct
from typing import Callable, Awaitable, Optional, Set

from .protocol import FrameType

logger = logging.getLogger("umdt.bridge.upstream")

# Callback type for handling received requests
RequestHandler = Callable[[bytes, "ClientSession"], Awaitable[Optional[bytes]]]


class ClientSession:
    """Represents a connected upstream client."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        session_id: int,
    ):
        self.reader = reader
        self.writer = writer
        self.session_id = session_id
        self.connected = True
        self._addr = writer.get_extra_info("peername")

    @property
    def address(self) -> str:
        if self._addr:
            return f"{self._addr[0]}:{self._addr[1]}"
        return "unknown"

    async def send(self, data: bytes) -> None:
        if not self.connected:
            return
        try:
            self.writer.write(data)
            await self.writer.drain()
        except Exception as e:
            logger.warning("Failed to send to client %s: %s", self.address, e)
            self.connected = False

    async def close(self) -> None:
        self.connected = False
        try:
            self.writer.close()
            await self.writer.wait_closed()
        except Exception:
            pass


class UpstreamServer:
    """TCP/RTU server accepting connections from Modbus Masters.

    For TCP mode: Starts a TCP server on the specified port.
    For RTU mode: Opens a serial port and acts as a slave device.
    """

    def __init__(
        self,
        frame_type: FrameType = FrameType.TCP,
        host: str = "0.0.0.0",
        port: int = 502,
        serial_port: Optional[str] = None,
        baudrate: int = 9600,
    ):
        self.frame_type = frame_type
        self.host = host
        self.port = port
        self.serial_port = serial_port
        self.baudrate = baudrate

        self._server: Optional[asyncio.Server] = None
        self._clients: Set[ClientSession] = set()
        self._request_handler: Optional[RequestHandler] = None
        self._running = False
        self._session_counter = 0
        self._serial_task: Optional[asyncio.Task] = None
        self._serial_writer: Optional[asyncio.StreamWriter] = None

        # Queue for serializing access to downstream (thread-safe for serial)
        self._request_queue: asyncio.Queue = asyncio.Queue()

    def set_request_handler(self, handler: RequestHandler) -> None:
        """Set the callback for handling incoming requests."""
        self._request_handler = handler

    async def start(self) -> None:
        """Start the upstream server."""
        self._running = True

        if self.frame_type == FrameType.TCP:
            await self._start_tcp()
        else:
            await self._start_serial()

    async def stop(self) -> None:
        """Stop the upstream server."""
        self._running = False

        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        if self._serial_task:
            self._serial_task.cancel()
            try:
                await self._serial_task
            except asyncio.CancelledError:
                pass

        # Close all client sessions
        for client in list(self._clients):
            await client.close()
        self._clients.clear()

        logger.info("Upstream server stopped")

    # --- TCP Mode ---

    async def _start_tcp(self) -> None:
        """Start TCP server."""
        self._server = await asyncio.start_server(
            self._handle_tcp_client,
            self.host,
            self.port,
        )
        addrs = ", ".join(str(s.getsockname()) for s in self._server.sockets)
        logger.info("Upstream TCP server listening on %s", addrs)

    async def _handle_tcp_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a connected TCP client."""
        self._session_counter += 1
        session = ClientSession(reader, writer, self._session_counter)
        self._clients.add(session)

        logger.info("Client connected: %s (session %d)", session.address, session.session_id)

        try:
            await self._tcp_client_loop(session)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.exception("Error handling client %s: %s", session.address, e)
        finally:
            self._clients.discard(session)
            await session.close()
            logger.info("Client disconnected: %s", session.address)

    async def _tcp_client_loop(self, session: ClientSession) -> None:
        """Read and process Modbus TCP frames from a client."""
        buffer = b""

        while self._running and session.connected:
            try:
                data = await asyncio.wait_for(session.reader.read(4096), timeout=60.0)
                if not data:
                    break
                buffer += data

                # Process complete frames in buffer
                while len(buffer) >= 7:  # Minimum MBAP header size
                    # Parse MBAP header to get frame length
                    length = struct.unpack(">H", buffer[4:6])[0]
                    frame_size = 6 + length  # MBAP header (6) + unit_id + PDU

                    if len(buffer) < frame_size:
                        break  # Wait for more data

                    # Extract complete frame
                    frame = buffer[:frame_size]
                    buffer = buffer[frame_size:]

                    logger.debug(
                        "Received TCP frame from %s: %s",
                        session.address,
                        frame.hex().upper(),
                    )

                    # Process through pipeline
                    if self._request_handler:
                        response = await self._request_handler(frame, session)
                        if response:
                            await session.send(response)

            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("Error reading from %s: %s", session.address, e)
                break

    # --- RTU (Serial) Mode ---

    async def _start_serial(self) -> None:
        """Start serial port listener for RTU mode."""
        if not self.serial_port:
            raise ValueError("Serial port not configured for RTU mode")

        try:
            import serial_asyncio
        except ImportError:
            raise ImportError("serial_asyncio required for RTU mode: pip install pyserial-asyncio")

        logger.info(
            "Starting upstream serial listener on %s @ %d baud",
            self.serial_port,
            self.baudrate,
        )

        reader, writer = await serial_asyncio.open_serial_connection(
            url=self.serial_port,
            baudrate=self.baudrate,
        )
        self._serial_writer = writer
        self._serial_task = asyncio.create_task(self._serial_loop(reader, writer))

    async def _serial_loop(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Read and process Modbus RTU frames from serial port."""
        buffer = b""
        last_byte_time = 0.0

        # RTU inter-frame gap (3.5 character times at 9600 baud ≈ 4ms)
        inter_frame_gap = max(0.004, (11 * 3.5) / self.baudrate)

        while self._running:
            try:
                data = await asyncio.wait_for(reader.read(256), timeout=0.1)
                if data:
                    import time
                    now = time.monotonic()

                    # Check for inter-frame gap (start of new frame)
                    if buffer and (now - last_byte_time) > inter_frame_gap:
                        # Process previous frame
                        await self._process_rtu_frame(buffer, writer)
                        buffer = b""

                    buffer += data
                    last_byte_time = now

            except asyncio.TimeoutError:
                # Timeout - if we have data, it's likely a complete frame
                if buffer:
                    await self._process_rtu_frame(buffer, writer)
                    buffer = b""
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("Serial read error: %s", e)
                await asyncio.sleep(0.1)

    async def _process_rtu_frame(
        self,
        frame: bytes,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Process a complete RTU frame."""
        if len(frame) < 4:
            return  # Too short

        logger.debug("Received RTU frame: %s", frame.hex().upper())

        # Create a pseudo-session for the serial port
        class SerialSession:
            def __init__(self, writer: asyncio.StreamWriter):
                self._writer = writer
                self.session_id = 0
                self.address = "serial"
                self.connected = True

            async def send(self, data: bytes) -> None:
                self._writer.write(data)
                await self._writer.drain()

        session = SerialSession(writer)

        if self._request_handler:
            response = await self._request_handler(frame, session)  # type: ignore
            if response:
                await session.send(response)

    # --- Properties ---

    @property
    def client_count(self) -> int:
        return len(self._clients)

    @property
    def is_running(self) -> bool:
        return self._running
