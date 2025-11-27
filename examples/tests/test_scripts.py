import asyncio
import random
import pytest

from examples.scripts import mock_counter, fault_injector, bridge_translate, bridge_mask_serial


class DummyLog:
    def __init__(self):
        self.entries = []

    def info(self, *args, **kwargs):
        self.entries.append(("info", args))

    def debug(self, *args, **kwargs):
        self.entries.append(("debug", args))

    def warning(self, *args, **kwargs):
        self.entries.append(("warning", args))

    def exception(self, *args, **kwargs):
        self.entries.append(("exception", args))


class DummyCtx:
    def __init__(self):
        self.log = DummyLog()
        self.state = {}
        self._writes = []
        self._scheduled = []

    def schedule_task(self, coro):
        # Do not actually run infinite background tasks in tests
        self._scheduled.append(coro)

    async def write_register(self, unit, address, value):
        self._writes.append((unit, address, value))
        # store last written value for convenience
        self.state[f"write:{unit}:{address}"] = value

    async def sleep(self, seconds):
        # fast-forward sleep in tests
        await asyncio.sleep(0)

    def make_response_exception(self, request, exception_code=1):
        return {"exception_for": getattr(request, 'address', None), "code": exception_code}


class DummyRequest:
    def __init__(self, function_code=3, address=None, unit_id=1):
        self.function_code = function_code
        self.address = address
        self.unit_id = unit_id


class DummyPDU:
    def __init__(self, data: bytes):
        self.data = data


class DummyResponse:
    def __init__(self, pdu: DummyPDU = None, address: int = None):
        self.pdu = pdu
        self.address = address


@pytest.mark.asyncio
async def test_mock_counter_on_request_exception():
    ctx = DummyCtx()
    req = DummyRequest(function_code=3, address=9999)
    res = await mock_counter.on_request(req, ctx)
    assert isinstance(res, dict)
    assert res.get("code") == 1


@pytest.mark.asyncio
async def test_fault_injector_drop_and_delay(monkeypatch):
    ctx = DummyCtx()
    req = DummyRequest(function_code=3, address=1, unit_id=5)

    # Force random.random() to return 0.05 -> drop
    monkeypatch.setattr(random, 'random', lambda: 0.05)
    res = await fault_injector.on_request(req, ctx)
    assert isinstance(res, dict)
    assert res.get('code') == 0xFF

    # Force random.random() to return 0.5 -> no drop; ensure sleep called
    sleeps = []
    async def fake_sleep(t):
        sleeps.append(t)
        await asyncio.sleep(0)

    monkeypatch.setattr(random, 'random', lambda: 0.5)
    monkeypatch.setattr(ctx, 'sleep', fake_sleep)

    res2 = await fault_injector.on_request(req, ctx)
    assert res2 is req  # Returns the request unchanged (pass through with delay)
    assert len(sleeps) == 1


@pytest.mark.asyncio
async def test_bridge_translate_ingress_hook():
    ctx = DummyCtx()
    req = DummyRequest(function_code=3, address=40005)
    out = await bridge_translate.ingress_hook(req, ctx)
    assert out.address == 1005
    assert any(e[0] == 'debug' for e in ctx.log.entries)


@pytest.mark.asyncio
async def test_bridge_mask_serial_upstream_response_hook():
    ctx = DummyCtx()
    pdu = DummyPDU(b"\x01\x02\x03\x04")
    res = DummyResponse(pdu=pdu, address=123)
    out = await bridge_mask_serial.upstream_response_hook(res, ctx)
    assert out.pdu.data == b"\x00\x00\x00\x00"
    assert any(e[0] == 'info' for e in ctx.log.entries)
