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
from typing import Callable, List, Any, Type, cast

_raw_hooks: List[Callable[[bytes], None]] = []

# Annotate dynamic bases so the type-checker knows these names will be classes
_BaseRtu: Type[Any]
_BaseSocket: Type[Any]


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
    # When pymodbus isn't present we fall back to a neutral base class.
    # Annotate as Type[Any] and cast to keep the type-checker satisfied
    _BaseRtu: Type[Any] = cast(Type[Any], object)
    _BaseSocket: Type[Any] = cast(Type[Any], object)


class UMDT_RtuFramer:
    """Permissive RTU framer implemented by composition.

    If pymodbus's framer is available we instantiate it and delegate parsing
    to the underlying object; otherwise the class remains a no-op sink that
    emits raw bytes to the registered hooks.
    """

    def __init__(self, *args, **kwargs):
        try:
            if isinstance(_BaseRtu, type):
                self._parent = _BaseRtu(*args, **kwargs)
            else:
                self._parent = None
        except Exception:
            self._parent = None

    def processIncomingPacket(self, data: bytes, callback=None):
        try:
            _emit_raw(bytes(data))
        except Exception:
            pass

        try:
            parent = getattr(self._parent, "processIncomingPacket", None)
            if parent is not None:
                return parent(data, callback)
        except Exception as exc:
            try:
                _emit_raw(b"ERROR:CRC:" + str(exc).encode())
            except Exception:
                pass
            return None


class UMDT_SocketFramer:
    """Permissive Socket framer implemented by composition.

    Delegates to the underlying pymodbus socket framer when available.
    """

    def __init__(self, *args, **kwargs):
        try:
            if isinstance(_BaseSocket, type):
                self._parent = _BaseSocket(*args, **kwargs)
            else:
                self._parent = None
        except Exception:
            self._parent = None

    def processIncomingPacket(self, data: bytes, callback=None):
        try:
            _emit_raw(bytes(data))
        except Exception:
            pass

        try:
            parent = getattr(self._parent, "processIncomingPacket", None)
            if parent is not None:
                return parent(data, callback)
        except Exception as exc:
            try:
                _emit_raw(b"ERROR:CRC:" + str(exc).encode())
            except Exception:
                pass
            return None
