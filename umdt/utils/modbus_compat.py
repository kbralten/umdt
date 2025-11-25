"""Helper utilities for invoking pymodbus client methods across API variants."""
from __future__ import annotations

import inspect
from typing import Any
import contextlib
import time


def _invoke_pymodbus_read(fn, address: int, count: int, unit: int):
    try:
        sig = inspect.signature(fn)
        params = list(sig.parameters.keys())
    except Exception:
        params = []

    unit_kw_options = ['unit', 'slave', 'device_id', 'device', 'unit_id']
    try:
        if 'count' in params:
            for kw in unit_kw_options:
                if kw in params:
                    try:
                        return fn(address, count=count, **{kw: unit})
                    except TypeError:
                        continue
            return fn(address, count=count)
    except TypeError:
        pass

    try:
        return fn(address, count, unit)
    except Exception:
        return fn(address, count)


def _invoke_pymodbus_write(fn, address: int, values: Any, unit: int):
    try:
        sig = inspect.signature(fn)
        params = list(sig.parameters.keys())
    except Exception:
        params = []

    unit_kw_options = ['unit', 'slave', 'device_id', 'device', 'unit_id']
    try:
        for kw in unit_kw_options:
            if kw in params:
                try:
                    return fn(address, values, **{kw: unit})
                except TypeError:
                    continue
        return fn(address, values)
    except TypeError:
        return fn(address, values)


def call_read_method(client: Any, method_name: str, address: int, count: int, unit: int):
    fn = getattr(client, method_name, None)
    if fn is None:
        raise AttributeError(f"Client does not support {method_name}")
    return _invoke_pymodbus_read(fn, address, count, unit)


def call_write_method(client: Any, method_name: str, address: int, values: Any, unit: int):
    fn = getattr(client, method_name, None)
    if fn is None:
        raise AttributeError(f"Client does not support {method_name}")
    return _invoke_pymodbus_write(fn, address, values, unit)


def invoke_method(client: Any, method_name: str, *args):
    """Invoke a client method in a compatibility-aware way.

    - For read_* methods: calls `call_read_method(client, method_name, address, count, unit)`
    - For write_* methods: calls `call_write_method(client, method_name, address, values, unit)`
    - Otherwise falls back to calling the attribute directly with given args.
    """
    if method_name.startswith('read_'):
        # expect (address, count, unit)
        if len(args) < 3:
            raise TypeError('read_* methods require (address, count, unit)')
        address, count, unit = args[0], args[1], args[2]
        return call_read_method(client, method_name, address, count, unit)

    if method_name.startswith('write_'):
        # expect (address, values, unit)
        if len(args) < 3:
            raise TypeError('write_* methods require (address, values, unit)')
        address, values, unit = args[0], args[1], args[2]
        return call_write_method(client, method_name, address, values, unit)

    fn = getattr(client, method_name, None)
    if fn is None:
        raise AttributeError(f"Client does not support {method_name}")
    return fn(*args)


def _import_clients():
    """Attempt to import common pymodbus client constructors from different versions."""
    candidates = [
        ('pymodbus.client', 'ModbusTcpClient', 'ModbusSerialClient'),
        ('pymodbus.client.sync', 'ModbusTcpClient', 'ModbusSerialClient'),
        ('pymodbus.client.tcp', 'ModbusTcpClient', None),
    ]
    for module, tcp_name, serial_name in candidates:
        try:
            mod = __import__(module, fromlist=[tcp_name])
        except Exception:
            continue
        tcp = getattr(mod, tcp_name, None)
        serial = getattr(mod, serial_name, None) if serial_name else None
        return tcp, serial
    return None, None


def create_client(kind: str = 'tcp', host: str | None = None, port: int | None = None, serial_port: str | None = None,
                  baudrate: int = 115200, timeout: float = 1.0, retries: int | None = None, **kwargs) -> Any:
    """Create and return a pymodbus client instance in a version-robust way.

    Args:
        kind: 'tcp' or 'serial'
        host/port: for TCP clients
        serial_port, baudrate: for serial clients
        timeout: seconds
        retries: optional retries parameter (used for serial to avoid port holding)
    """
    ModbusTcpClient, ModbusSerialClient = _import_clients()
    if kind == 'tcp':
        if ModbusTcpClient is None:
            raise ImportError('pymodbus ModbusTcpClient not available')
        params = {'host': host, 'port': port, 'timeout': timeout}
        params.update(kwargs)
        return ModbusTcpClient(**{k: v for k, v in params.items() if v is not None})

    if kind == 'serial':
        if ModbusSerialClient is None:
            raise ImportError('pymodbus ModbusSerialClient not available')
        params = {'method': 'rtu', 'port': serial_port, 'baudrate': baudrate, 'timeout': timeout}
        if retries is not None:
            params['retries'] = retries
        params.update(kwargs)
        # Filter params to only those accepted by the constructor to avoid
        # unexpected keyword argument errors across pymodbus versions.
        try:
            sig = inspect.signature(ModbusSerialClient.__init__)
            paramspecs = sig.parameters
            # If the constructor accepts **kwargs, pass all non-None params
            if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in paramspecs.values()):
                filtered = {k: v for k, v in params.items() if v is not None}
            else:
                allowed = [p for p in paramspecs.keys() if p != 'self' and paramspecs[p].kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)]
                filtered = {k: v for k, v in params.items() if k in allowed and v is not None}
        except Exception:
            # Fallback: keep non-None params
            filtered = {k: v for k, v in params.items() if v is not None}

        return ModbusSerialClient(**filtered)

    raise ValueError('kind must be "tcp" or "serial"')


def close_client(client: Any) -> None:
    """Close/cleanup a pymodbus client instance safely."""
    if client is None:
        return
    try:
        close = getattr(client, 'close', None)
        if callable(close):
            close()
            return
    except Exception:
        pass
    # best-effort socket close
    sock = getattr(client, 'socket', None)
    try:
        if sock:
            sock.close()
    except Exception:
        pass


def read_holding_registers(client: Any, address: int, count: int, unit: int = 1):
    return call_read_method(client, 'read_holding_registers', address, count, unit)


def read_input_registers(client: Any, address: int, count: int, unit: int = 1):
    return call_read_method(client, 'read_input_registers', address, count, unit)


def read_coils(client: Any, address: int, count: int, unit: int = 1):
    return call_read_method(client, 'read_coils', address, count, unit)


def read_discrete_inputs(client: Any, address: int, count: int, unit: int = 1):
    return call_read_method(client, 'read_discrete_inputs', address, count, unit)


def write_registers(client: Any, address: int, values: Any, unit: int = 1):
    # write_registers / write_register - accept lists or single value
    return call_write_method(client, 'write_registers', address, values, unit)


def write_register(client: Any, address: int, value: Any, unit: int = 1):
    return call_write_method(client, 'write_register', address, value, unit)


def write_coil(client: Any, address: int, value: bool, unit: int = 1):
    return call_write_method(client, 'write_coil', address, value, unit)


def write_coils(client: Any, address: int, values: Any, unit: int = 1):
    return call_write_method(client, 'write_coils', address, values, unit)