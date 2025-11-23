import asyncio
import random
from .base import TransportInterface

class MockTransport(TransportInterface):
    def __init__(self):
        self.connected = False
        self.rx_queue = asyncio.Queue()

    async def connect(self):
        self.connected = True
        print("MockTransport: Connected")

    async def disconnect(self):
        self.connected = False
        print("MockTransport: Disconnected")

    async def send(self, data: bytes):
        if not self.connected:
            raise RuntimeError("Not connected")
        
        # Simulate transmission delay
        delay = random.uniform(0.5, 1.5) # Increased delay to make it visible
        await asyncio.sleep(delay)
        
        # Echo back reversed data
        response = data[::-1]
        await self.rx_queue.put(response)

    async def receive(self) -> bytes:
        if not self.connected:
            # Wait a bit if not connected to avoid busy loop in consumer
            await asyncio.sleep(0.1)
            raise RuntimeError("Not connected")
        return await self.rx_queue.get()
