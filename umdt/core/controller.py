import asyncio
import time
import struct
from typing import List, Callable, Dict, Optional, Tuple, Sequence, Union
from umdt.transports.base import TransportInterface
from umdt.transports.manager import ConnectionManager
from umdt.database.logging import DBLogger
from umdt.core.data_types import (
    DATA_TYPE_PROPERTIES,
    DataType,
)
import logging
logger = logging.getLogger("umdt.controller")

logger = logging.getLogger("umdt.controller")


class CoreController:
    def __init__(self, transport: Optional[TransportInterface] = None, uri: Optional[str] = None, *, db_path: Optional[str] = None, logger: Optional[DBLogger] = None):
        self.transport = transport
        self.uri = uri
        self.logs: List[Dict] = []
        self.observers: List[Callable[[Dict], None]] = []
        self.running = False
        self._rx_task = None
        # Resource locking for scanner vs user-initiated commands
        self.transport_lock: asyncio.Lock = asyncio.Lock()
        self._scanner_task = None
        self._scanner_resume: asyncio.Event = asyncio.Event()
        self._scanner_resume.set()
        self._scanner_running = False
        self._use_manager = False
        self._manager = None
        # DB logger (optional)
        self._logger: Optional[DBLogger] = logger
        self._db_path = db_path

        if transport is None and uri is not None:
            self._use_manager = True
            self._manager = ConnectionManager.instance()
            # subscribe to manager status updates
            self._manager.add_status_callback(self._on_status)

        # lazily create DBLogger if a path was provided
        if self._logger is None and self._db_path:
            self._logger = DBLogger(db_path=self._db_path)

    def add_observer(self, callback: Callable[[Dict], None]):
        self.observers.append(callback)

    def _log(self, direction: str, data: bytes):
        entry = {"direction": direction, "data": data.hex().upper()}
        self.logs.append(entry)
        for observer in self.observers:
            try:
                observer(entry)
            except Exception:
                pass
        # enqueue into DBLogger if available
        if self._logger:
            pkt = {"timestamp": time.time(), "direction": direction, "raw": data, "parsed": None}
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._logger.enqueue(pkt))
            except RuntimeError:
                # no running loop in this thread; try submitting to logger's loop if present
                if getattr(self._logger, "loop", None):
                    try:
                        asyncio.run_coroutine_threadsafe(self._logger.enqueue(pkt), self._logger.loop)
                    except Exception:
                        pass

    def _on_status(self, msg: str):
        # status messages from ConnectionManager
        entry = {"direction": "STATUS", "data": msg}
        self.logs.append(entry)
        for observer in self.observers:
            try:
                observer(entry)
            except Exception:
                pass
        if self._logger:
            pkt = {"timestamp": time.time(), "direction": "STATUS", "raw": msg.encode("utf-8"), "parsed": None}
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._logger.enqueue(pkt))
            except RuntimeError:
                if getattr(self._logger, "loop", None):
                    try:
                        asyncio.run_coroutine_threadsafe(self._logger.enqueue(pkt), self._logger.loop)
                    except Exception:
                        pass

    async def start(self):
        self.running = True
        if self._use_manager and self._manager and self.uri:
            await self._manager.start(self.uri)
            # start DB logger before rx loop so incoming packets are captured
            if self._logger:
                await self._logger.start()
            self._rx_task = asyncio.create_task(self._rx_loop())
        else:
            await self.transport.connect()
            if self._logger:
                await self._logger.start()
            self._rx_task = asyncio.create_task(self._rx_loop())

    # Scanner management
    def start_scanner(self, interval: float = 1.0):
        """Start the background scanner task which acquires the transport lock
        for short batches to allow user-initiated commands to take priority.
        """
        if self._scanner_task and not self._scanner_task.done():
            return
        self._scanner_running = True
        self._scanner_task = asyncio.create_task(self._scanner_loop(interval))

    async def stop_scanner(self):
        self._scanner_running = False
        if self._scanner_task:
            self._scanner_task.cancel()
            try:
                await self._scanner_task
            except asyncio.CancelledError:
                pass
        self._scanner_resume.set()

    async def _scanner_loop(self, interval: float):
        while self._scanner_running:
            await self._scanner_resume.wait()
            try:
                async with self.transport_lock:
                    # Placeholder: single scan iteration; keep short
                    await asyncio.sleep(0)  # real scan work goes here
                # yield between batches
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(0.1)

    class _WriteAccess:
        def __init__(self, controller: "CoreController"):
            self._c = controller

        async def __aenter__(self):
            # pause scanner before acquiring lock
            try:
                self._c._scanner_resume.clear()
            except Exception:
                pass
            await self._c.transport_lock.acquire()
            return self

        async def __aexit__(self, exc_type, exc, tb):
            try:
                self._c.transport_lock.release()
            except Exception:
                pass
            try:
                self._c._scanner_resume.set()
            except Exception:
                pass

    def request_write_access(self):
        """Return an async context manager to acquire exclusive write access.

        Usage:
            async with controller.request_write_access():
                await controller.send_data(...)
        """
        return CoreController._WriteAccess(self)

    async def stop(self):
        self.running = False
        if self._rx_task:
            self._rx_task.cancel()
            try:
                await self._rx_task
            except asyncio.CancelledError:
                pass

        if self._use_manager and self._manager:
            await self._manager.stop()
        else:
            await self.transport.disconnect()
        if self._logger:
            try:
                await self._logger.stop()
            except Exception:
                pass

    async def send_data(self, data: bytes):
        self._log("TX", data)
        logger.debug("send_data: sending %d bytes: %s", len(data), data.hex().upper())
        if self._use_manager and self._manager:
            await self._manager.send(data)
        else:
            await self.transport.send(data)

    async def _rx_loop(self):
        while self.running:
            try:
                if self._use_manager and self._manager:
                    data = await self._manager.receive()
                else:
                    data = await self.transport.receive()
                self._log("RX", data)
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(0.1)

    # --- Modbus protocol helpers ---

    def _build_modbus_request(self, unit: int, function: int, data: bytes) -> bytes:
        """Build a simple Modbus RTU/TCP request frame.

        For RTU: unit + function + data + CRC
        For TCP: MBAP header + unit + function + data
        We'll use a simplified TCP-style frame for now (no CRC, assume transport handles framing).
        """
        # Simple Modbus TCP ADU: transaction_id(2) + protocol_id(2) + length(2) + unit(1) + function(1) + data
        # For simplicity we omit MBAP and assume raw RTU-style frames work over our transports
        frame = bytes([unit, function]) + data
        # Append CRC16 for RTU compatibility
        crc = self._modbus_crc16(frame)
        return frame + struct.pack('<H', crc)

    def _modbus_crc16(self, data: bytes) -> int:
        """Compute Modbus CRC16."""
        crc = 0xFFFF
        for byte in data:
            crc ^= byte
            for _ in range(8):
                if crc & 0x0001:
                    crc = (crc >> 1) ^ 0xA001
                else:
                    crc >>= 1
        return crc

    def _parse_modbus_response(self, frame: bytes, expected_unit: int, expected_function: int) -> Tuple[bool, Optional[bytes]]:
        """Parse a Modbus RTU response frame.

        Returns (success, data) where data is the payload (registers, etc.) or None on error.
        """
        if len(frame) < 5:  # min: unit + function + 1-byte + CRC(2)
            return False, None

        # Check CRC
        received_crc = struct.unpack('<H', frame[-2:])[0]
        computed_crc = self._modbus_crc16(frame[:-2])
        if received_crc != computed_crc:
            return False, None

        unit = frame[0]
        function = frame[1]

        if unit != expected_unit:
            return False, None

        # Check for exception response (function code has high bit set)
        if function & 0x80:
            return False, None

        if function != expected_function:
            return False, None

        # Extract data (everything between function code and CRC)
        data = frame[2:-2]
        return True, data

    async def modbus_read_holding_registers(self, unit: int, address: int, count: int) -> Optional[List[int]]:
        """Backward compatible helper for FC03."""

        return await self._modbus_read_registers_fc(unit, address, count, 0x03)

    async def modbus_write_registers(self, unit: int, address: int, values: List[int]) -> bool:
        """Backward compatible helper for FC16."""

        return await self._modbus_write_registers_fc(unit, address, values, 0x10)

    async def read_data(
        self,
        unit: int,
        address: int,
        count: int,
        data_type: DataType,
    ) -> Optional[List[Union[int, bool]]]:
        props = DATA_TYPE_PROPERTIES[data_type]
        if props.read_function is None:
            return None
        if props.bit_based:
            return await self._modbus_read_bits_fc(unit, address, count, props.read_function)
        return await self._modbus_read_registers_fc(unit, address, count, props.read_function)

    async def write_data(
        self,
        unit: int,
        address: int,
        values: Sequence[Union[int, bool]],
        data_type: DataType,
    ) -> bool:
        props = DATA_TYPE_PROPERTIES[data_type]
        if not props.writable or props.write_function is None:
            return False
        if props.bit_based:
            bool_values = [bool(v) for v in values]
            return await self._modbus_write_bits_fc(unit, address, bool_values, props.write_function)
        int_values = [int(v) & 0xFFFF for v in values]
        return await self._modbus_write_registers_fc(unit, address, int_values, props.write_function)

    async def _send_modbus_request(self, unit: int, function: int, payload: bytes) -> Optional[bytes]:
        if not self.running:
            return None

        request = self._build_modbus_request(unit, function, payload)

        frame: Optional[bytes] = None
        if self._use_manager and self._manager and self.uri and self.uri.startswith("serial://"):
            # If we're running inside the asyncio event loop, prefer the
            # manager's async send/receive helper to avoid using the
            # thread-safe blocking wrapper which calls
            # `asyncio.run_coroutine_threadsafe` (not allowed from the
            # running loop). Use the async helper so GUI-initiated reads
            # work correctly.
            try:
                frame = await self._manager._send_and_receive(request, timeout=2.0)
            except Exception:
                logger.exception("_send_modbus_request: manager async send/receive failed")
                return None
        else:
            async with self.request_write_access():
                await self.send_data(request)
                try:
                    frame = await asyncio.wait_for(self._read_one_frame(), timeout=2.0)
                except asyncio.TimeoutError:
                    return None

        success, response_payload = self._parse_modbus_response(frame, unit, function)
        if not success:
            return None
        return response_payload

    async def _modbus_read_registers_fc(
        self, unit: int, address: int, count: int, function: int
    ) -> Optional[List[int]]:
        payload = await self._send_modbus_request(unit, function, struct.pack('>HH', address, count))
        if payload is None:
            return None

        if len(payload) < 1:
            return None
        byte_count = payload[0]
        if len(payload) < 1 + byte_count:
            return None

        registers: List[int] = []
        for i in range(count):
            offset = 1 + i * 2
            if offset + 1 < len(payload):
                registers.append(struct.unpack('>H', payload[offset:offset+2])[0])
        return registers

    async def _modbus_read_bits_fc(
        self, unit: int, address: int, count: int, function: int
    ) -> Optional[List[bool]]:
        payload = await self._send_modbus_request(unit, function, struct.pack('>HH', address, count))
        if payload is None:
            return None
        if len(payload) < 1:
            return None
        byte_count = payload[0]
        if len(payload) < 1 + byte_count:
            return None

        bits: List[bool] = []
        data = payload[1:1 + byte_count]
        for byte in data:
            for bit_index in range(8):
                if len(bits) >= count:
                    break
                bits.append(bool(byte & (1 << bit_index)))
            if len(bits) >= count:
                break
        return bits

    async def _modbus_write_registers_fc(
        self, unit: int, address: int, values: Sequence[int], function: int
    ) -> bool:
        if not values:
            return False
        count = len(values)
        byte_count = count * 2
        payload = struct.pack('>HHB', address, count, byte_count)
        for value in values:
            payload += struct.pack('>H', value & 0xFFFF)
        response = await self._send_modbus_request(unit, function, payload)
        return response is not None

    async def _modbus_write_bits_fc(
        self, unit: int, address: int, values: Sequence[bool], function: int
    ) -> bool:
        if not values:
            return False

        byte_array = self._pack_coil_bytes(values)
        payload = struct.pack('>HHB', address, len(values), len(byte_array)) + byte_array
        response = await self._send_modbus_request(unit, function, payload)
        return response is not None

    def _pack_coil_bytes(self, values: Sequence[bool]) -> bytes:
        buffer = bytearray()
        current = 0
        bit_idx = 0
        for value in values:
            if value:
                current |= 1 << bit_idx
            bit_idx += 1
            if bit_idx == 8:
                buffer.append(current)
                current = 0
                bit_idx = 0
        if bit_idx:
            buffer.append(current)
        return bytes(buffer)

    async def _read_one_frame(self) -> bytes:
        """Read one complete Modbus frame from the transport.

        This is a simplified implementation that reads available bytes and attempts
        to detect frame boundaries. For production use, a proper state machine with
        inter-frame timeouts would be needed.
        """
        # For now, wait for the manager/transport to return one chunk
        # (assumes transport.receive() returns one complete frame or a reasonable chunk)
        if self._use_manager and self._manager:
            frame = await self._manager.receive()
            logger.debug("_read_one_frame: received %s bytes: %s", len(frame), frame.hex().upper())
            return frame
        else:
            frame = await self.transport.receive()
            logger.debug("_read_one_frame: received %s bytes: %s", len(frame), frame.hex().upper())
            return frame
