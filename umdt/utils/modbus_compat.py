"""Helper utilities for invoking pymodbus client methods across API variants."""
from __future__ import annotations

import inspect
from typing import Any


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