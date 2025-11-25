import asyncio
import types
import sys
import time

import pytest

from umdt.core.prober import Prober, TargetSpec
from umdt.core.data_types import DataType


def _make_fake_pymodbus_module(success_port: int):
    """Create a fake `pymodbus.client` module with ModbusTcpClient/SerialClient.

    The fake TCP client returns a FakeResponse from `read_holding_registers` when
    constructed with the configured success_port; otherwise returns None.
    """
    mod = types.ModuleType("pymodbus.client")

    class FakeResponse:
        def __init__(self, registers=None, error=False):
            self.registers = registers
            self._error = error

        def isError(self):
            return self._error

        def __str__(self):
            return f"FakeResponse(regs={self.registers}, err={self._error})"

    class ModbusTcpClient:
        def __init__(self, host, port=502, timeout=None):
            self.host = host
            self.port = int(port) if port is not None else None
            self.timeout = timeout

        def connect(self):
            return True

        def close(self):
            return True

        def read_holding_registers(self, address, count=1, **kwargs):
            # succeed if port matches success_port
            if self.port == success_port:
                return FakeResponse(registers=[123])
            # simulate protocol exception for a specific other port
            if self.port == success_port + 1:
                return FakeResponse(registers=None, error=True)
            return None

    class ModbusSerialClient:
        def __init__(self, port=None, baudrate=None, timeout=None):
            self.port = port
            self.baudrate = baudrate

        def connect(self):
            return True

        def close(self):
            return True

        def read_holding_registers(self, address, count=1, **kwargs):
            return None

    mod.ModbusTcpClient = ModbusTcpClient
    mod.ModbusSerialClient = ModbusSerialClient
    return mod


@pytest.mark.asyncio
async def test_blocking_probe_tcp_client_success_and_exception(monkeypatch):
    # Inject fake pymodbus.client module
    fake_mod = _make_fake_pymodbus_module(success_port=5502)
    monkeypatch.setitem(sys.modules, "pymodbus.client", fake_mod)

    # Use a Prober instance and call _blocking_probe directly for several URIs
    p = Prober()
    target = TargetSpec(datatype=DataType.HOLDING, address=0)

    # Success case (port 5502)
    alive, summary = p._blocking_probe("tcp://127.0.0.1:5502?unit=1", target, {}, timeout_s=0.1)
    assert alive is True
    assert summary is not None and "response" in summary or "FakeResponse" in summary

    # Protocol exception case (port 5503) should be considered alive
    alive2, summary2 = p._blocking_probe("tcp://127.0.0.1:5503?unit=1", target, {}, timeout_s=0.1)
    assert alive2 is True
    assert summary2 is not None

    # No response (other port) should be dead
    alive3, summary3 = p._blocking_probe("tcp://127.0.0.1:5599?unit=1", target, {}, timeout_s=0.1)
    assert alive3 is False


@pytest.mark.asyncio
async def test_concurrency_limit(monkeypatch):
    # Patch the blocking probe to simulate variable-duration work and track concurrency
    max_active = 0
    active = 0

    lock = asyncio.Lock()

    def fake_blocking(self, uri, target, params, timeout_s):
        nonlocal max_active, active
        # increment active count
        active += 1
        max_active = max(max_active, active)
        # simulate work
        time.sleep(0.05)
        active -= 1
        return False, "no"

    monkeypatch.setattr(Prober, "_blocking_probe", fake_blocking)

    p = Prober(concurrency=3)
    combos = [f"tcp://127.0.0.1:{5500 + i}?unit=1" for i in range(12)]
    target = TargetSpec(datatype=DataType.HOLDING, address=0)

    # Run the prober and ensure the observed parallelism never exceeds the configured concurrency
    await p.run(combos, target)
    assert max_active <= 3


@pytest.mark.asyncio
async def test_serial_sequential_probing(monkeypatch):
    """Verify that serial combos are probed sequentially (no overlap)."""
    active_serial = 0
    max_serial_active = 0
    probe_log = []

    def fake_blocking(self, uri, target, params, timeout_s):
        nonlocal active_serial, max_serial_active
        if 'serial' in uri:
            active_serial += 1
            max_serial_active = max(max_serial_active, active_serial)
            probe_log.append(('serial-start', uri, active_serial))
            time.sleep(0.02)
            active_serial -= 1
            probe_log.append(('serial-end', uri, active_serial))
        else:
            probe_log.append(('tcp', uri))
        return False, "no"

    monkeypatch.setattr(Prober, "_blocking_probe", fake_blocking)

    p = Prober(concurrency=4)
    # Mix serial and TCP combos
    combos = [
        {"serial": "COM3", "baud": 9600, "unit": 1},
        {"serial": "COM4", "baud": 9600, "unit": 1},
        {"host": "127.0.0.1", "port": 502, "unit": 1},
        {"serial": "COM5", "baud": 9600, "unit": 1},
    ]
    target = TargetSpec(datatype=DataType.HOLDING, address=0)

    await p.run(combos, target)
    # Ensure serial probes never overlapped (max concurrent serial = 1)
    assert max_serial_active == 1
