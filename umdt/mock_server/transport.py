from __future__ import annotations

import logging
from typing import Optional

from pymodbus.constants import ExcCodes
from pymodbus.datastore import ModbusBaseDeviceContext, ModbusServerContext
from pymodbus.server import ModbusSerialServer, ModbusTcpServer

from umdt.core.data_types import DataType

from .core import MockDevice, RequestDropped, RegisterAccessError

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

    def __init__(self, device: MockDevice, unit_id: int = 1):
        super().__init__()
        self._device = device
        self._unit_id = unit_id

    def _dtype(self, func_code: int) -> DataType:
        dtype = _FUNC_TO_TYPE.get(func_code)
        if not dtype:
            raise ValueError(f"Unsupported function code {func_code}")
        return dtype

    async def async_getValues(self, func_code: int, address: int, count: int = 1):
        try:
            dtype = self._dtype(func_code)
            values = await self._device.read(dtype, address, count)
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
            # Emit event for illegal address
            await self._device.diagnostics.emit(
                "tcp",
                f"Client read illegal address: func={func_code}, addr={address}",
                func_code=func_code,
                address=address,
            )
            return ExcCodes.ILLEGAL_ADDRESS

    async def async_setValues(self, func_code: int, address: int, values):
        try:
            dtype = self._dtype(func_code)
            await self._device.write(dtype, address, values)
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
            # Emit event for illegal address
            await self._device.diagnostics.emit(
                "tcp",
                f"Client write illegal address: func={func_code}, addr={address}",
                func_code=func_code,
                address=address,
            )
            return ExcCodes.ILLEGAL_ADDRESS

    @staticmethod
    def _exception_from_code(code: int) -> ExcCodes:
        try:
            return ExcCodes(code)
        except Exception:
            return ExcCodes.DEVICE_FAILURE


class TransportCoordinator:
    """Manage TCP or Serial Modbus server transports for the mock device."""

    def __init__(self, device: MockDevice, unit_id: int = 1) -> None:
        self._device = device
        self._unit_id = unit_id
        self._context = ModbusServerContext({unit_id: DeviceBackedContext(device, unit_id)}, single=False)
        self._server: Optional[ModbusTcpServer | ModbusSerialServer] = None

    async def start_tcp(self, host: str = "127.0.0.1", port: int = 1502) -> None:
        await self.stop()
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
        if self._server is None:
            return
        logger.info("Stopping mock server transport")
        await self._server.shutdown()
        self._server = None
        # Emit event for server stop
        await self._device.diagnostics.emit(
            "tcp",  # Could be tcp or serial, but we don't track that
            "Server stopped",
        )

    async def restart(self, *, host: Optional[str] = None, port: Optional[int] = None, serial_port: Optional[str] = None, baudrate: Optional[int] = None) -> None:
        if serial_port:
            await self.start_serial(serial_port, baudrate or 9600)
        elif host or port:
            await self.start_tcp(host or "127.0.0.1", port or 1502)
