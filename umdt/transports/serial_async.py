import asyncio
import logging
import math
from typing import Optional

import serial_asyncio

from .base import TransportInterface


class SerialTransport(TransportInterface):
    def __init__(self, port: str, baudrate: int = 19200, inter_byte_timeout: Optional[float] = None):
        self.port = port
        self.baudrate = baudrate
        # Calculate default t3.5 if not provided
        if inter_byte_timeout is None:
            if self.baudrate > 19200:
                # Fixed 1.75ms for high speeds as per Modbus spec recommendations
                self.inter_byte_timeout = 0.00175
            else:
                # 3.5 chars * 11 bits/char / baud
                self.inter_byte_timeout = (3.5 * 11) / self.baudrate
        else:
            self.inter_byte_timeout = inter_byte_timeout

        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self.connected = False
        self.rx_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._rx_task: Optional[asyncio.Task] = None

    async def connect(self):
        if self.connected:
            return
        # serial_asyncio.open_serial_connection returns (reader, writer)
        try:
            logger = logging.getLogger("umdt.transports.serial")
            logger.debug("SerialTransport.connect: opening port=%s baud=%s timeout=%s", 
                         self.port, self.baudrate, self.inter_byte_timeout)
        except Exception:
            pass
        
        # Pass inter_byte_timeout to the underlying serial driver
        self.reader, self.writer = await serial_asyncio.open_serial_connection(
            url=self.port, 
            baudrate=self.baudrate,
            inter_byte_timeout=self.inter_byte_timeout
        )
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
                # Read up to 4096 bytes.
                # With inter_byte_timeout set, this should return partial reads 
                # when a silence interval is detected by the driver.
                data = await self.reader.read(4096)
                if not data:
                    # EOF or disconnected
                    self.connected = False
                    break
                await self.rx_queue.put(data)
        except asyncio.CancelledError:
            return
        except Exception:
            self.connected = False
            return
