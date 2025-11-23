import asyncio
from umdt.transports.manager import ConnectionManager
from umdt.core.controller import CoreController
from umdt.transports.mock import MockTransport
from umdt.protocols.framers import register_raw_hook, UMDT_RtuFramer


def test_manager_send_receive():
    async def run():
        cm = ConnectionManager.instance()
        try:
            await cm.start("mock://")
            await cm.send(bytes.fromhex("AABBCCDD"))
            data = await asyncio.wait_for(cm.receive(), timeout=3.0)
            assert data == bytes.fromhex("DDCCBBAA")
        finally:
            await cm.stop()
            # tear down singleton to avoid cross-test interference
            ConnectionManager._instance = None

    asyncio.run(asyncio.wait_for(run(), timeout=10))


def test_corecontroller_logs():
    entries = []

    async def run():
        # Use direct MockTransport to avoid ConnectionManager singleton races in tests
        transport = MockTransport()
        ctrl = CoreController(transport=transport)
        ctrl.add_observer(lambda e: entries.append(e))
        try:
            await ctrl.start()
            await asyncio.sleep(0.05)
            await ctrl.send_data(bytes.fromhex("AABBCCDD"))
            await asyncio.sleep(1.5)
        finally:
            await ctrl.stop()

    asyncio.run(asyncio.wait_for(run(), timeout=15))

    directions = [e["direction"] for e in entries]
    assert "TX" in directions
    assert "RX" in directions


def test_request_write_access_and_scanner():
    async def run():
        # scanner and write access are CoreController-local; use MockTransport
        transport = MockTransport()
        ctrl = CoreController(transport=transport)
        try:
            await ctrl.start()
            ctrl.start_scanner(interval=0.05)
            await asyncio.sleep(0.05)

            async with ctrl.request_write_access():
                assert ctrl.transport_lock.locked()

            await ctrl.stop_scanner()
        finally:
            await ctrl.stop()

    asyncio.run(asyncio.wait_for(run(), timeout=15))


def test_framer_emits_raw():
    seen = []
    register_raw_hook(lambda b: seen.append(b))
    f = UMDT_RtuFramer()
    # call with raw bytes; framer should emit them to hooks
    f.processIncomingPacket(b"\x01\x02\x03", None)
    assert any(b"\x01\x02\x03" in s for s in seen)
