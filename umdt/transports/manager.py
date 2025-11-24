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
            rest = uri[len("tcp://"):]
            if ":" in rest:
                host, port = rest.split(":", 1)
                return TcpTransport(host, int(port))
            else:
                return TcpTransport(rest, 502)
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
        self._connect_task = asyncio.create_task(self._reconnect_loop())
        # wait for first successful connection (best-effort, small timeout)
        try:
            await asyncio.wait_for(self._connected_event.wait(), timeout=5.0)
        except Exception:
            # timeout or cancelled — caller can decide how to proceed
            self._notify("connect timeout/wait finished")

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

            async with self.lock:
                if self.transport:
                    return await self.transport.receive()

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

        async with self.lock:
            await self.send(data)
            return await asyncio.wait_for(self.receive(), timeout=timeout)

    def send_and_receive_blocking(self, data: bytes, timeout: float = 2.0) -> bytes:
        """Blocking helper usable from threads: schedules an async send/receive on the manager loop.

        Raises Exception on errors or if the manager loop is not available.
        """
        if not self._loop:
            raise RuntimeError("ConnectionManager loop not initialized")
        fut = asyncio.run_coroutine_threadsafe(self._send_and_receive(data, timeout=timeout), self._loop)
        return fut.result(timeout + 1.0)
