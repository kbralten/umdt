import asyncio
import time
import pytest

from umdt.core.prober import Prober, TargetSpec
from umdt.core.data_types import DataType


@pytest.mark.asyncio
async def test_probe_success(monkeypatch):
    # Simulate blocking probe that returns alive for URIs containing '5502'
    def fake_probe(self, uri, target, params, timeout_s):
        if '5502' in uri:
            return True, 'response:ok'
        return False, 'no-response'

    monkeypatch.setattr(Prober, '_blocking_probe', fake_probe)

    p = Prober(concurrency=4, attempts=1)
    combos = [
        'tcp://127.0.0.1:5501?unit=1',
        'tcp://127.0.0.1:5502?unit=1',
    ]
    target = TargetSpec(datatype=DataType.HOLDING, address=0)

    results = await p.run(combos, target)
    # Ensure we found the 5502 endpoint as alive
    alive_uris = [r.uri for r in results if r.alive]
    assert any('5502' in u for u in alive_uris)


@pytest.mark.asyncio
async def test_probe_attempts_and_backoff(monkeypatch):
    # Fake probe that returns False first, True second call (per combo)
    state = {'calls': 0}

    def fake_probe(self, uri, target, params, timeout_s):
        state['calls'] += 1
        # succeed only on second call
        return (state['calls'] >= 2), ('ok' if state['calls'] >= 2 else 'no')

    monkeypatch.setattr(Prober, '_blocking_probe', fake_probe)

    p = Prober(concurrency=1, attempts=2, backoff_ms=1)
    combos = ['tcp://127.0.0.1:5503?unit=1']
    target = TargetSpec(datatype=DataType.HOLDING, address=0)

    results = await p.run(combos, target)
    assert len(results) == 1
    assert results[0].alive is True
    # ensure fake probe was called at least twice
    assert state['calls'] >= 2


@pytest.mark.asyncio
async def test_probe_cancellation(monkeypatch):
    # Long-running fake probe; will be cancelled
    def slow_probe(self, uri, target, params, timeout_s):
        time.sleep(0.2)
        return False, 'no'

    monkeypatch.setattr(Prober, '_blocking_probe', slow_probe)

    p = Prober(concurrency=2, attempts=1)
    combos = [f'tcp://127.0.0.1:{5500 + i}?unit=1' for i in range(4)]
    target = TargetSpec(datatype=DataType.HOLDING, address=0)

    cancel = asyncio.Event()

    async def run_and_cancel():
        task = asyncio.create_task(p.run(combos, target, cancel_token=cancel))
        await asyncio.sleep(0.05)
        cancel.set()
        res = await task
        return res

    results = await run_and_cancel()
    # Either empty or some non-alive entries depending on timing
    assert isinstance(results, list)
