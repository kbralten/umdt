import asyncio
import pytest
from umdt.transports.mock import MockTransport
from umdt.transports.passive import PassiveTransport


def test_passive_send_raises():
    async def run():
        m = MockTransport()
        await m.connect()
        p = PassiveTransport(m)
        with pytest.raises(RuntimeError):
            await p.send(b"\x01\x02")
        await m.disconnect()

    asyncio.run(run())


def test_passive_write_flush_raises():
    async def run():
        m = MockTransport()
        await m.connect()
        p = PassiveTransport(m)
        with pytest.raises(RuntimeError):
            await p.write(b"\x01")
        with pytest.raises(RuntimeError):
            await p.flush()
        await m.disconnect()

    asyncio.run(run())
