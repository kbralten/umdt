import asyncio
import math
import random
from typing import Optional
from urllib.parse import urlparse
import logging

logger = logging.getLogger("umdt.transports.manager")

from .base import TransportInterface
from .mock import MockTransport
from .tcp import TcpTransport
# import serial transport lazily to avoid requiring pyserial-asyncio at import time


class ConnectionManager:
    _instance = None

    def __init__(self):
        self.transport: Optional[TransportInterface] = None
        self.uri: Optional[str] = None
        self._connect_task: Optional[asyncio.Task] = None
        self._stop = False
        self.lock = asyncio.Lock()
        self._connected_event: asyncio.Event = asyncio.Event()
        self.status_callbacks = []
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def add_status_callback(self, cb):
        self.status_callbacks.append(cb)

    def _notify(self, msg: str):
        for cb in self.status_callbacks:
            try:
                cb(msg)
            except Exception:
                pass

    def create_transport_from_uri(self, uri: str) -> TransportInterface:
        # Simple URI parsing
        if uri.startswith("mock://"):
            return MockTransport()
        if uri.startswith("tcp://"):
            # Use urlparse to correctly handle query strings (e.g. ?unit=1)
            parsed = urlparse(uri)
            host = parsed.hostname or parsed.path
            port = parsed.port or 502
            return TcpTransport(host, int(port))
        if uri.startswith("serial://"):
            # lazy import to avoid hard dependency at module import time
            try:
                from .serial_async import SerialTransport
            except Exception as e:
                # Provide a clearer runtime error when serial_asyncio is not available
                raise RuntimeError(
                    "Serial transport requires 'pyserial-asyncio'. Install with: pip install pyserial-asyncio"
                ) from e

            # Use urlparse so query strings (e.g. ?unit=1) don't contaminate the baud
            parsed = urlparse(uri)
            netloc = parsed.netloc or parsed.path.lstrip('/')
            parts = netloc.split(":") if netloc else []
            port = parts[0] if parts else ""
            baud = int(parts[1]) if len(parts) > 1 and parts[1] else 19200
            logger.debug("create_transport_from_uri: serial port=%s baud=%s", port, baud)
            return SerialTransport(port, baud)
        raise ValueError("Unsupported URI scheme")

    async def start(self, uri: str):
        self.uri = uri
        self._stop = False
        # remember the loop we're running on so synchronous callers can
        # schedule coroutines back onto it via run_coroutine_threadsafe
        try:
            self._loop = asyncio.get_running_loop()
        except Exception:
            self._loop = None
        if self._connect_task and not self._connect_task.done():
            return
        # Start the reconnect loop in background and return immediately.
        # Callers who want to wait for a connection can await
        # `manager._connected_event.wait()` themselves; awaiting here caused
        # re-entrancy issues when used from the GUI event loop.
        self._connect_task = asyncio.create_task(self._reconnect_loop())
        # Best-effort notify that connection attempt has started
        self._notify(f"connect initiated to {self.uri}")

    async def stop(self):
        self._stop = True
        if self._connect_task:
            self._connect_task.cancel()
            try:
                await self._connect_task
            except asyncio.CancelledError:
                pass
        if self.transport:
            await self.transport.disconnect()
            self.transport = None
        try:
            self._connected_event.clear()
        except Exception:
            pass

    async def send(self, data: bytes):
        # wait for a connection to be established (best-effort)
        deadline = asyncio.get_event_loop().time() + 5.0
        while True:
            try:
                await asyncio.wait_for(self._connected_event.wait(), timeout=1.0)
            except Exception:
                pass

            async with self.lock:
                if self.transport:
                    await self.transport.send(data)
                    return

            if asyncio.get_event_loop().time() > deadline:
                raise RuntimeError("No transport connected")

    async def receive(self) -> bytes:
        deadline = asyncio.get_event_loop().time() + 5.0
        while True:
            try:
                await asyncio.wait_for(self._connected_event.wait(), timeout=1.0)
            except Exception:
                pass

            # Grab a reference to the transport under the lock, but do NOT hold
            # the lock while awaiting the transport's receive. Holding the lock
            # here caused a deadlock: the rx logging loop would acquire the
            # lock and then block on receive, preventing send() from acquiring
            # the lock to transmit requests. Fetching the transport reference
            # and releasing the lock before awaiting avoids that interlock.
            transport = None
            async with self.lock:
                transport = self.transport

            if transport:
                return await transport.receive()

            if asyncio.get_event_loop().time() > deadline:
                raise RuntimeError("No transport connected")

    async def _reconnect_loop(self):
        attempt = 0
        while not self._stop:
            try:
                self._notify(f"Connecting to {self.uri} (attempt {attempt})")
                t = self.create_transport_from_uri(self.uri)
                await t.connect()
                self.transport = t
                try:
                    self._connected_event.set()
                except Exception:
                    pass
                self._notify("connected")

                # stay connected until error or stop
                while not self._stop and getattr(t, "connected", True):
                    await asyncio.sleep(0.5)
                if self._stop:
                    try:
                        await t.disconnect()
                    except Exception:
                        pass
                    break
            except Exception as e:
                self._notify(f"connect error: {e}")
                try:
                    self._connected_event.clear()
                except Exception:
                    pass

            # exponential backoff with jitter
            attempt += 1
            backoff = min(60, (2 ** attempt) + random.uniform(0, 1) * attempt)
            await asyncio.sleep(backoff)

    async def _send_and_receive(self, data: bytes, timeout: float = 2.0) -> bytes:
        """Async helper: send data and wait for a receive, using the manager's lock."""
        # Ensure transport is connected
        if not self.transport:
            raise RuntimeError("No transport connected")

        # Use the public send/receive helpers. These manage the lock
        # appropriately; do not attempt to hold the lock across both calls
        # because that would deadlock when another coroutine (e.g. the
        # controller rx logger) is concurrently awaiting receive().
        await self.send(data)
        return await asyncio.wait_for(self.receive(), timeout=timeout)

    def send_and_receive_blocking(self, data: bytes, timeout: float = 2.0) -> bytes:
        """Blocking helper usable from threads: schedules an async send/receive on the manager loop.

        Raises Exception on errors or if the manager loop is not available.
        """
        # This helper is intended to be called from OTHER threads (not the
        # manager's event loop). Calling it from within the manager's running
        # loop (e.g., the GUI event loop) will attempt to synchronously wait
        # for a coroutine on the same loop and raise a RuntimeError. Detect
        # that situation and raise a clear error instead of attempting the
        # dangerous call.
        try:
            # If a running loop exists in this thread, don't proceed.
            asyncio.get_running_loop()
        except RuntimeError:
            # No running loop in this thread: safe to schedule onto manager loop
            if not self._loop:
                raise RuntimeError("ConnectionManager loop not initialized")
            fut = asyncio.run_coroutine_threadsafe(self._send_and_receive(data, timeout=timeout), self._loop)
            return fut.result(timeout + 1.0)
        else:
            # We are running inside an event loop — this API is invalid here.
            raise RuntimeError("send_and_receive_blocking cannot be called from the event loop thread; use the async send/receive helpers instead")
