import asyncio
from typing import Optional

from .base import TransportInterface


class TcpTransport(TransportInterface):
    def __init__(self, host: str, port: int, reconnect: bool = False):
        self.host = host
        self.port = port
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self.connected = False
        self.rx_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._rx_task: Optional[asyncio.Task] = None

    async def connect(self):
        if self.connected:
            return
        self.reader, self.writer = await asyncio.open_connection(self.host, self.port)
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
            self.writer.close()
            try:
                await self.writer.wait_closed()
            except Exception:
                pass

    async def send(self, data: bytes):
        if not self.connected or not self.writer:
            raise RuntimeError("TcpTransport: not connected")
        self.writer.write(data)
        await self.writer.drain()

    async def receive(self) -> bytes:
        return await self.rx_queue.get()

    async def _rx_loop(self):
        try:
            while self.connected and self.reader:
                data = await self.reader.read(4096)
                if not data:
                    # remote closed
                    self.connected = False
                    break
                await self.rx_queue.put(data)
        except asyncio.CancelledError:
            return
        except Exception:
            self.connected = False
            return
