from abc import ABC, abstractmethod

class TransportInterface(ABC):
    @abstractmethod
    async def connect(self):
        pass

    @abstractmethod
    async def disconnect(self):
        pass

    @abstractmethod
    async def send(self, data: bytes):
        pass

    @abstractmethod
    async def receive(self) -> bytes:
        pass
