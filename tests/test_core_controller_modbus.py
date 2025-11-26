import asyncio
import struct

from umdt.core.controller import CoreController
from umdt.transports.base import TransportInterface


class FakeTransport(TransportInterface):
    def __init__(self, responses=None):
        self._responses = list(responses or [])
        self.sent = []
        self.connected = False

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    async def send(self, data: bytes):
        # record sent frames
        self.sent.append(data)

    async def receive(self) -> bytes:
        # return next prepared response or wait briefly
        if not self._responses:
            await asyncio.sleep(0.01)
            return b""
        return self._responses.pop(0)


def make_response(controller: CoreController, unit: int, function: int, payload: bytes) -> bytes:
    frame = bytes([unit, function]) + payload
    crc = controller._modbus_crc16(frame)
    return frame + struct.pack('<H', crc)


def test_modbus_read_holding_registers():
    # Prepare expected register values
    unit = 1
    address = 1
    regs = [0x1234, 0xABCD]
    byte_count = len(regs) * 2
    payload = bytes([byte_count]) + b"".join(r.to_bytes(2, 'big') for r in regs)

    # Create controller with fake transport returning the response
    fake = FakeTransport()
    ctrl = CoreController(transport=fake)
    # Build response frame using controller CRC helper
    resp = make_response(ctrl, unit, 0x03, payload)
    fake._responses.append(resp)

    # Mark controller as running so modbus methods proceed
    ctrl.running = True

    result = asyncio.run(ctrl.modbus_read_holding_registers(unit, address, len(regs)))
    assert result == regs


def test_modbus_write_registers():
    unit = 1
    address = 10
    values = [0x1111, 0x2222]

    # Build FC16 success response: echo address and count
    ctrl = CoreController(transport=None)
    fake = FakeTransport()
    ctrl = CoreController(transport=fake)
    payload = struct.pack('>HH', address, len(values))
    resp = make_response(ctrl, unit, 0x10, payload)
    fake._responses.append(resp)

    ctrl.running = True

    success = asyncio.run(ctrl.modbus_write_registers(unit, address, values))
    assert success is True

    # Validate the sent frame contains the register bytes we expected
    assert fake.sent, "No frame was sent"
    sent = fake.sent[0]
    # strip CRC (last 2 bytes) and unit/function header
    body = sent[2:-2]
    # ensure register bytes appear in the body
    for v in values:
        assert v.to_bytes(2, 'big') in body
