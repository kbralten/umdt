from typing import Optional
from .base import TransportInterface


class PassiveTransport(TransportInterface):
    """Wrap an existing TransportInterface and disable all write operations.

    Intended for "sniffer" mode where the application must not drive the bus.
    All read/connection operations are proxied to the wrapped transport; calls
    to `send` raise a RuntimeError.
    """

    def __init__(self, wrapped: TransportInterface):
        self._wrapped = wrapped

    async def connect(self):
        return await self._wrapped.connect()

    async def disconnect(self):
        return await self._wrapped.disconnect()

    async def send(self, data: bytes):
        raise RuntimeError("Operation Forbidden in Sniffer Mode")

    # Some transports expose `write`/`flush` for lower-level control; block them too.
    async def write(self, data: bytes):
        raise RuntimeError("Operation Forbidden in Sniffer Mode")

    async def flush(self):
        raise RuntimeError("Operation Forbidden in Sniffer Mode")

    async def receive(self) -> bytes:
        return await self._wrapped.receive()

    # Optional: expose the underlying transport when callers need access
    @property
    def wrapped(self) -> TransportInterface:
        return self._wrapped
