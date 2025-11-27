from __future__ import annotations

import logging
import struct
from pathlib import Path
from typing import List, Optional, Union

from pymodbus.constants import ExcCodes
from pymodbus.datastore import ModbusBaseDeviceContext, ModbusServerContext
from pymodbus.server import ModbusSerialServer, ModbusTcpServer

from umdt.core.data_types import DataType
from umdt.core.pcap import Direction, PcapWriter
from umdt.core.script_engine import ExceptionResponse, ScriptRequest

from .core import MockDevice, RequestDropped, RegisterAccessError
from .script_hook import MockServerScriptHook

logger = logging.getLogger(__name__)


_FUNC_TO_TYPE = {
    1: DataType.COIL,
    2: DataType.DISCRETE,
    3: DataType.HOLDING,
    4: DataType.INPUT,
    5: DataType.COIL,
    6: DataType.HOLDING,
    15: DataType.COIL,
    16: DataType.HOLDING,
    22: DataType.HOLDING,
    23: DataType.HOLDING,
}


class DeviceBackedContext(ModbusBaseDeviceContext):
    """Modbus context that proxies requests into the MockDevice."""

    def __init__(self, device: MockDevice, unit_id: int = 1, pcap_writer: Optional[PcapWriter] = None,
                 script_hook: Optional[MockServerScriptHook] = None):
        super().__init__()
        self._device = device
        self._unit_id = unit_id
        self._pcap_writer = pcap_writer
        self._script_hook = script_hook

    def set_pcap_writer(self, writer: Optional[PcapWriter]) -> None:
        """Set or clear the PCAP writer for traffic capture."""
        self._pcap_writer = writer

    def set_script_hook(self, hook: Optional[MockServerScriptHook]) -> None:
        """Set or clear the script hook for request/response interception."""
        self._script_hook = hook

    def _dtype(self, func_code: int) -> DataType:
        dtype = _FUNC_TO_TYPE.get(func_code)
        if not dtype:
            raise ValueError(f"Unsupported function code {func_code}")
        return dtype

    async def async_getValues(self, func_code: int, address: int, count: int = 1):
        # Build a pseudo-MBAP request frame for PCAP logging
        # Format: [func(1), addr(2), count(2)]
        request_pdu = struct.pack(">BHH", func_code, address, count)
        request_frame = self._build_mbap_frame(request_pdu)
        await self._log_pcap(request_frame, Direction.INBOUND)

        # Run request through script hooks (if configured)
        if self._script_hook and self._script_hook.has_hooks():
            result = await self._script_hook.process_request(
                func_code=func_code,
                address=address,
                count=count,
                unit_id=self._unit_id,
            )
            if result is None:
                # Script blocked the request
                logger.debug("Request blocked by script hook")
                return ExcCodes.GATEWAY_NO_RESPONSE
            elif isinstance(result, ExceptionResponse):
                # Script returned an exception
                exc_pdu = struct.pack(">BB", func_code | 0x80, result.code)
                await self._log_pcap(self._build_mbap_frame(exc_pdu), Direction.OUTBOUND)
                await self._device.diagnostics.emit(
                    "tcp",
                    f"Script exception: func={func_code}, addr={address}, code={result.code}",
                    func_code=func_code,
                    address=address,
                    exception_code=result.code,
                )
                return self._exception_from_code(result.code)
            elif isinstance(result, ScriptRequest):
                # Use possibly modified request parameters
                address = result.address
                count = result.count

        try:
            dtype = self._dtype(func_code)
            values = await self._device.read(dtype, address, count)
            # Build response frame for PCAP
            # For read responses: [func, byte_count, data...]
            if isinstance(values, list):
                byte_count = len(values) * 2
                response_pdu = struct.pack(">BB", func_code, byte_count)
                for v in values:
                    response_pdu += struct.pack(">H", v & 0xFFFF)
            else:
                response_pdu = struct.pack(">BB", func_code, 0)
            response_frame = self._build_mbap_frame(response_pdu)
            await self._log_pcap(response_frame, Direction.OUTBOUND)

            # Emit event for successful read
            await self._device.diagnostics.emit(
                "tcp",  # Transport type (could be "serial" but we don't have that info here)
                f"Client read: func={func_code}, addr={address}, count={count}",
                func_code=func_code,
                address=address,
                count=count,
                data_type=dtype.name,
            )
            return values
        except RegisterAccessError as exc:
            # Log exception response
            exc_pdu = struct.pack(">BB", func_code | 0x80, exc.code)
            await self._log_pcap(self._build_mbap_frame(exc_pdu), Direction.OUTBOUND)
            # Emit event for error response
            await self._device.diagnostics.emit(
                "tcp",
                f"Client read error: func={func_code}, addr={address}, exception={exc.code}",
                func_code=func_code,
                address=address,
                exception_code=exc.code,
            )
            return self._exception_from_code(exc.code)
        except RequestDropped:
            # Emit event for dropped request
            await self._device.diagnostics.emit(
                "tcp",
                f"Client read dropped: func={func_code}, addr={address}",
                func_code=func_code,
                address=address,
            )
            return ExcCodes.GATEWAY_NO_RESPONSE
        except ValueError:
            # Log exception response for illegal address
            exc_pdu = struct.pack(">BB", func_code | 0x80, 2)  # Illegal Data Address
            await self._log_pcap(self._build_mbap_frame(exc_pdu), Direction.OUTBOUND)
            # Emit event for illegal address
            await self._device.diagnostics.emit(
                "tcp",
                f"Client read illegal address: func={func_code}, addr={address}",
                func_code=func_code,
                address=address,
            )
            return ExcCodes.ILLEGAL_ADDRESS

    async def async_setValues(self, func_code: int, address: int, values):
        # Build a pseudo-MBAP request frame for PCAP logging
        if isinstance(values, list):
            if func_code in (5, 6):  # Single write
                request_pdu = struct.pack(">BHH", func_code, address, values[0] if values else 0)
            else:  # Multiple write (FC 15, 16)
                qty = len(values)
                byte_count = qty * 2
                request_pdu = struct.pack(">BHHB", func_code, address, qty, byte_count)
                for v in values:
                    request_pdu += struct.pack(">H", v & 0xFFFF)
        else:
            request_pdu = struct.pack(">BHH", func_code, address, values)
        request_frame = self._build_mbap_frame(request_pdu)
        await self._log_pcap(request_frame, Direction.INBOUND)

        # Run request through script hooks (if configured)
        values_list = values if isinstance(values, list) else [values]
        if self._script_hook and self._script_hook.has_hooks():
            result = await self._script_hook.process_request(
                func_code=func_code,
                address=address,
                count=len(values_list),
                unit_id=self._unit_id,
                values=values_list,
            )
            if result is None:
                # Script blocked the request
                logger.debug("Write request blocked by script hook")
                return ExcCodes.GATEWAY_NO_RESPONSE
            elif isinstance(result, ExceptionResponse):
                # Script returned an exception
                exc_pdu = struct.pack(">BB", func_code | 0x80, result.code)
                await self._log_pcap(self._build_mbap_frame(exc_pdu), Direction.OUTBOUND)
                await self._device.diagnostics.emit(
                    "tcp",
                    f"Script exception: func={func_code}, addr={address}, code={result.code}",
                    func_code=func_code,
                    address=address,
                    exception_code=result.code,
                )
                return self._exception_from_code(result.code)
            elif isinstance(result, ScriptRequest):
                # Use possibly modified request parameters
                address = result.address
                if result.values is not None:
                    values = result.values

        try:
            dtype = self._dtype(func_code)
            await self._device.write(dtype, address, values)
            # Build response frame (echo for single, addr+qty for multiple)
            if func_code in (5, 6):
                response_pdu = struct.pack(">BHH", func_code, address, values[0] if isinstance(values, list) and values else values)
            else:
                qty = len(values) if isinstance(values, list) else 1
                response_pdu = struct.pack(">BHH", func_code, address, qty)
            response_frame = self._build_mbap_frame(response_pdu)
            await self._log_pcap(response_frame, Direction.OUTBOUND)

            # Emit event for successful write
            value_str = str(values) if not isinstance(values, list) else f"[{len(values)} values]"
            await self._device.diagnostics.emit(
                "tcp",
                f"Client write: func={func_code}, addr={address}, values={value_str}",
                func_code=func_code,
                address=address,
                count=len(values) if isinstance(values, list) else 1,
                data_type=dtype.name,
            )
            return None
        except RegisterAccessError as exc:
            # Log exception response
            exc_pdu = struct.pack(">BB", func_code | 0x80, exc.code)
            await self._log_pcap(self._build_mbap_frame(exc_pdu), Direction.OUTBOUND)
            # Emit event for error response
            await self._device.diagnostics.emit(
                "tcp",
                f"Client write error: func={func_code}, addr={address}, exception={exc.code}",
                func_code=func_code,
                address=address,
                exception_code=exc.code,
            )
            return self._exception_from_code(exc.code)
        except RequestDropped:
            # Emit event for dropped request
            await self._device.diagnostics.emit(
                "tcp",
                f"Client write dropped: func={func_code}, addr={address}",
                func_code=func_code,
                address=address,
            )
            return ExcCodes.GATEWAY_NO_RESPONSE
        except ValueError:
            # Log exception response for illegal address
            exc_pdu = struct.pack(">BB", func_code | 0x80, 2)  # Illegal Data Address
            await self._log_pcap(self._build_mbap_frame(exc_pdu), Direction.OUTBOUND)
            # Emit event for illegal address
            await self._device.diagnostics.emit(
                "tcp",
                f"Client write illegal address: func={func_code}, addr={address}",
                func_code=func_code,
                address=address,
            )
            return ExcCodes.ILLEGAL_ADDRESS

    def _build_mbap_frame(self, pdu: bytes) -> bytes:
        """Build an MBAP-like frame for PCAP logging."""
        # MBAP header: trans_id(2), proto_id(2), length(2), unit_id(1)
        length = len(pdu) + 1  # PDU + unit_id
        header = struct.pack(">HHHB", 0, 0, length, self._unit_id)
        return header + pdu

    async def _log_pcap(self, frame: bytes, direction: Direction) -> None:
        """Log a frame to the PCAP writer if configured."""
        if self._pcap_writer:
            await self._pcap_writer.write_packet_async(
                data=frame,
                direction=direction,
                protocol=PcapWriter.PROTO_MODBUS_TCP,
            )

    @staticmethod
    def _exception_from_code(code: int) -> ExcCodes:
        try:
            return ExcCodes(code)
        except Exception:
            return ExcCodes.DEVICE_FAILURE


class TransportCoordinator:
    """Manage TCP or Serial Modbus server transports for the mock device."""

    def __init__(
        self,
        device: MockDevice,
        unit_id: int = 1,
        pcap_path: Optional[Union[str, Path]] = None,
        scripts: Optional[List[Union[str, Path]]] = None,
    ) -> None:
        self._device = device
        self._unit_id = unit_id
        self._pcap_writer: Optional[PcapWriter] = None
        self._pcap_path = Path(pcap_path) if pcap_path else None
        self._script_hook: Optional[MockServerScriptHook] = None

        # Load scripts if provided
        if scripts:
            self._script_hook = MockServerScriptHook(scripts=scripts, name="mock_server")
            logger.info("Loaded %d script(s) for mock server", len(scripts))
        
        self._device_context = DeviceBackedContext(device, unit_id, script_hook=self._script_hook)
        self._context = ModbusServerContext({unit_id: self._device_context}, single=False)
        self._server: Optional[ModbusTcpServer | ModbusSerialServer] = None

    @property
    def script_hook(self) -> Optional[MockServerScriptHook]:
        """Get the script hook instance (if configured)."""
        return self._script_hook

    def set_pcap_path(self, path: Optional[Union[str, Path]]) -> None:
        """Set the PCAP output path. Call before starting the server."""
        self._pcap_path = Path(path) if path else None

    async def _start_pcap(self) -> None:
        """Start PCAP logging if a path is configured."""
        if self._pcap_path and not self._pcap_writer:
            self._pcap_writer = PcapWriter(self._pcap_path)
            self._pcap_writer.open()
            self._device_context.set_pcap_writer(self._pcap_writer)
            logger.info("PCAP logging started: %s", self._pcap_path)

    async def _stop_pcap(self) -> None:
        """Stop PCAP logging."""
        if self._pcap_writer:
            self._device_context.set_pcap_writer(None)
            await self._pcap_writer.flush_async()
            await self._pcap_writer.aclose()
            logger.info(
                "PCAP logging stopped: %d packets, %d bytes",
                self._pcap_writer.packet_count,
                self._pcap_writer.bytes_written,
            )
            self._pcap_writer = None

    @property
    def pcap_stats(self) -> dict:
        """Get PCAP logging statistics."""
        if not self._pcap_writer:
            return {"packets": 0, "bytes": 0, "active": False}
        return {
            "packets": self._pcap_writer.packet_count,
            "bytes": self._pcap_writer.bytes_written,
            "active": True,
        }

    async def start_tcp(self, host: str = "127.0.0.1", port: int = 1502) -> None:
        await self.stop()
        await self._start_pcap()
        # Start periodic hooks if configured
        if self._script_hook:
            await self._script_hook.start_periodic_hooks()
        logger.info("Starting mock server TCP listener on %s:%s", host, port)
        server = ModbusTcpServer(self._context, address=(host, port))
        await server.serve_forever(background=True)
        self._server = server
        # Emit event for server start
        await self._device.diagnostics.emit(
            "tcp",
            f"Server started on {host}:{port} (unit_id={self._unit_id})",
            host=host,
            port=port,
            unit_id=self._unit_id,
        )

    async def start_serial(self, port: str, baudrate: int = 9600) -> None:
        await self.stop()
        await self._start_pcap()
        # Start periodic hooks if configured
        if self._script_hook:
            await self._script_hook.start_periodic_hooks()
        logger.info("Starting mock server serial listener on %s baud=%s", port, baudrate)
        server = ModbusSerialServer(
            self._context,
            port=port,
            baudrate=baudrate,
        )
        await server.serve_forever(background=True)
        self._server = server
        # Emit event for server start
        await self._device.diagnostics.emit(
            "serial",
            f"Server started on {port} @ {baudrate} baud (unit_id={self._unit_id})",
            port=port,
            baudrate=baudrate,
            unit_id=self._unit_id,
        )

    async def stop(self) -> None:
        # Stop script hooks first
        if self._script_hook:
            await self._script_hook.stop()
        if self._server is None:
            await self._stop_pcap()
            return
        logger.info("Stopping mock server transport")
        await self._server.shutdown()
        self._server = None
        await self._stop_pcap()
        # Emit event for server stop
        await self._device.diagnostics.emit(
            "tcp",  # Could be tcp or serial, but we don't track that
            "Server stopped",
        )

    def get_stats(self) -> dict:
        """Get combined stats for PCAP and scripts."""
        stats = {"pcap": self.pcap_stats}
        if self._script_hook:
            stats["scripts"] = self._script_hook.get_stats()
        return stats

    async def restart(self, *, host: Optional[str] = None, port: Optional[int] = None, serial_port: Optional[str] = None, baudrate: Optional[int] = None) -> None:
        if serial_port:
            await self.start_serial(serial_port, baudrate or 9600)
        elif host or port:
            await self.start_tcp(host or "127.0.0.1", port or 1502)
