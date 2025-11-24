from __future__ import annotations

import json
from pathlib import Path

import pytest

from umdt.core.data_types import DataType
from umdt.mock_server import MockDevice, load_config
from umdt.mock_server.config import MockServerConfig
from umdt.mock_server.core import RequestDropped, RegisterAccessError
from umdt.mock_server.models import RegisterGroup, RegisterRule, ResponseMode, ValueScript


def test_load_config_parses_groups_and_rules(tmp_path: Path) -> None:
    cfg_path = tmp_path / "mock.json"
    payload = {
        "unit_id": 7,
        "groups": [
            {
                "name": "Holding",
                "type": "holding",
                "start": 40001,
                "length": 4,
                "writable": True,
                "description": "Demo",
            }
        ],
        "rules": {
            "40002": {"mode": "frozen-value", "forced_value": 42},
        },
        "scripts": [
            {"address": 40003, "expression": "value + 1"}
        ],
        "faults": {"latency_ms": 10, "enabled": True},
    }
    cfg_path.write_text(json.dumps(payload), encoding="utf-8")

    cfg = load_config(cfg_path)

    assert cfg.unit_id == 7
    assert len(cfg.groups) == 1
    group = cfg.groups[0]
    assert group.name == "Holding"
    assert cfg.default_rules["40002"].forced_value == 42
    assert 40003 in cfg.value_scripts
    assert cfg.fault_profile["latency_ms"] == 10


@pytest.mark.asyncio
async def test_mock_device_rules_and_scripts() -> None:
    cfg = MockServerConfig(
        unit_id=1,
        groups=[RegisterGroup(name="regs", data_type=DataType.HOLDING, start=0, length=10, writable=True)],
        default_rules={"5": RegisterRule(response_mode=ResponseMode.FROZEN_VALUE, forced_value=555)},
        value_scripts={1: ValueScript(expression="value + 10")},
        fault_profile={"enabled": False},
    )
    device = MockDevice(cfg)

    # Script applies even when raw register is zero
    values = await device.read(DataType.HOLDING, 1, 1)
    assert values[0] == 10

    # Frozen value rule
    frozen = await device.read(DataType.HOLDING, 5, 1)
    assert frozen[0] == 555

    # Ignore writes rule prevents changes
    await device.apply_rule(2, RegisterRule(response_mode=ResponseMode.IGNORE_WRITE))
    await device.write(DataType.HOLDING, 2, [1234])
    persisted = await device.read(DataType.HOLDING, 2, 1)
    assert persisted[0] == 0

    # Exception rule raises
    await device.apply_rule(3, RegisterRule(response_mode=ResponseMode.EXCEPTION, exception_code=4))
    with pytest.raises(RegisterAccessError):
        await device.read(DataType.HOLDING, 3, 1)


@pytest.mark.asyncio
async def test_diagnostics_drop_request() -> None:
    cfg = MockServerConfig(
        unit_id=1,
        groups=[RegisterGroup(name="regs", data_type=DataType.COIL, start=0, length=2, writable=True)],
        fault_profile={"enabled": True, "drop_rate_pct": 100},
    )
    device = MockDevice(cfg)
    with pytest.raises(RequestDropped):
        await device.read(DataType.COIL, 0, 1)