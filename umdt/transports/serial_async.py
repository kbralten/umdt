import asyncio
from typing import Optional

import serial_asyncio

from .base import TransportInterface


class SerialTransport(TransportInterface):
    def __init__(self, port: str, baudrate: int = 19200):
        self.port = port
        self.baudrate = baudrate
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self.connected = False
        self.rx_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._rx_task: Optional[asyncio.Task] = None

    async def connect(self):
        if self.connected:
            return
        # serial_asyncio.open_serial_connection returns (reader, writer)
        self.reader, self.writer = await serial_asyncio.open_serial_connection(url=self.port, baudrate=self.baudrate)
        self.connected = True
        self._rx_task = asyncio.create_task(self._rx_loop())

    async def disconnect(self):
        self.connected = False
        if self._rx_task:
            self._rx_task.cancel()
            try:
                await self._rx_task
            except asyncio.CancelledError:
                pass
        if self.writer:
            try:
                self.writer.transport.close()
            except Exception:
                pass

    async def send(self, data: bytes):
        if not self.connected or not self.writer:
            raise RuntimeError("SerialTransport: not connected")
        self.writer.write(data)
        await self.writer.drain()

    async def receive(self) -> bytes:
        return await self.rx_queue.get()

    async def _rx_loop(self):
        try:
            while self.connected and self.reader:
                data = await self.reader.read(4096)
                if not data:
                    self.connected = False
                    break
                await self.rx_queue.put(data)
        except asyncio.CancelledError:
            return
        except Exception:
            self.connected = False
            return
