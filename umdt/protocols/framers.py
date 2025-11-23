"""UMDT framers: permissive framers that capture raw bytes and provide soft-error handling.

These wrap pymodbus framers when available; if pymodbus isn't installed the classes remain
lightweight and safe to import.

Usage:
  from umdt.protocols.framers import register_raw_hook, UMDT_RtuFramer, UMDT_SocketFramer
  register_raw_hook(my_callback)  # my_callback(raw_bytes or dict)

The framers call registered hooks with the raw incoming bytes before delegating to
the underlying pymodbus logic. On CRC or parse errors they emit a diagnostic hook
instead of raising, allowing the listener task to continue.
"""
from typing import Callable, List

_raw_hooks: List[Callable] = []


def register_raw_hook(cb: Callable):
    """Register a callback that receives raw incoming bytes or diagnostic tuples.

    Callback signature should accept one argument. It will be called with either
    raw bytes (preferred) or a dict like {"error": "crc", "data": b"..."}.
    """
    _raw_hooks.append(cb)


def _emit_raw(data: bytes):
    for cb in list(_raw_hooks):
        try:
            cb(data)
        except Exception:
            # never raise from hooks
            pass


try:
    from pymodbus.framer.rtu_framer import ModbusRtuFramer as _BaseRtu
    from pymodbus.framer.socket_framer import ModbusSocketFramer as _BaseSocket
except Exception:
    _BaseRtu = object
    _BaseSocket = object


class UMDT_RtuFramer(_BaseRtu):
    """Permissive RTU framer: emits raw bytes to hooks and soft-fails on parse errors."""

    def __init__(self, *args, **kwargs):
        try:
            super().__init__(*args, **kwargs)
        except Exception:
            # If parent constructor not available (pymodbus missing), allow construction
            pass

    def processIncomingPacket(self, data: bytes, callback=None):
        # Emit raw bytes first for logging/inspection.
        try:
            _emit_raw(bytes(data))
        except Exception:
            pass

        # Delegate to parent if available, but swallow CRC/parse errors and notify hooks.
        try:
            parent = getattr(super(), "processIncomingPacket", None)
            if parent is not None:
                return parent(data, callback)
        except Exception as exc:
            try:
                _emit_raw(b"ERROR:CRC:" + str(exc).encode())
            except Exception:
                pass
            # swallow to avoid crashing the listener
            return None


class UMDT_SocketFramer(_BaseSocket):
    """Permissive Socket framer: emits raw bytes to hooks and soft-fails on parse errors."""

    def __init__(self, *args, **kwargs):
        try:
            super().__init__(*args, **kwargs)
        except Exception:
            pass

    def processIncomingPacket(self, data: bytes, callback=None):
        try:
            _emit_raw(bytes(data))
        except Exception:
            pass

        try:
            parent = getattr(super(), "processIncomingPacket", None)
            if parent is not None:
                return parent(data, callback)
        except Exception as exc:
            try:
                _emit_raw(b"ERROR:CRC:" + str(exc).encode())
            except Exception:
                pass
            return None
