from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import json

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    yaml = None

from umdt.core.data_types import DataType, parse_data_type
from .models import RegisterGroup, RegisterRule, ResponseMode, ValueScript


@dataclass(slots=True)
class TransportConfig:
    """Transport selection for the mock server."""

    tcp_host: Optional[str] = None
    tcp_port: Optional[int] = None
    serial_port: Optional[str] = None
    serial_baud: int = 9600

    def validate(self) -> None:
        tcp_enabled = self.tcp_host is not None or self.tcp_port is not None
        serial_enabled = self.serial_port is not None
        if tcp_enabled and serial_enabled:
            raise ValueError("TCP and serial transports are mutually exclusive")
        if not tcp_enabled and not serial_enabled:
            raise ValueError("Select either TCP host/port or serial port")


@dataclass(slots=True)
class MockServerConfig:
    """Top-level configuration for the diagnostic mock server."""

    unit_id: int = 1
    groups: List[RegisterGroup] = field(default_factory=list)
    default_rules: Dict[str, RegisterRule] = field(default_factory=dict)
    value_scripts: Dict[int, ValueScript] = field(default_factory=dict)
    latency_ms: int = 0
    latency_jitter_pct: float = 0.0
    fault_profile: Dict[str, Any] = field(default_factory=dict)
    transport: Optional[TransportConfig] = None
    random_seed: Optional[int] = None


def _to_group(data: Dict[str, Any]) -> RegisterGroup:
    group = RegisterGroup(
        name=data["name"],
        data_type=parse_data_type(data.get("type", "holding")),
        start=int(data["start"]),
        length=int(data["length"]),
        writable=bool(data.get("writable", True)),
        description=data.get("description", ""),
        metadata=dict(data.get("metadata", {})),
    )
    return group


def load_config(path: str | Path) -> MockServerConfig:
    """Parse a YAML/JSON config file into a structured config object."""

    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(file_path)

    text = file_path.read_text(encoding="utf-8")
    if file_path.suffix.lower() in {".yaml", ".yml"}:
        if yaml is None:
            raise RuntimeError("PyYAML is required to load YAML configs")
        raw = yaml.safe_load(text)
    else:
        raw = json.loads(text)

    if not isinstance(raw, dict):
        raise ValueError("Configuration must be an object/dict")

    groups = [_to_group(item) for item in raw.get("groups", [])]

    default_rules: Dict[str, RegisterRule] = {}
    for addr_txt, rule_data in (raw.get("rules", {}) or {}).items():
        rule = RegisterRule(
            response_mode=ResponseMode(rule_data.get("mode", ResponseMode.NORMAL)),
            forced_value=rule_data.get("forced_value"),
            exception_code=rule_data.get("exception_code"),
            ignore_write=bool(rule_data.get("ignore_write", False)),
            metadata=dict(rule_data.get("metadata", {})),
        )
        default_rules[str(addr_txt)] = rule

    value_scripts: Dict[int, ValueScript] = {}
    for entry in raw.get("scripts", []) or []:
        script = ValueScript(
            expression=entry["expression"],
            description=entry.get("description", ""),
            enabled=bool(entry.get("enabled", True)),
        )
        value_scripts[int(entry["address"])] = script

    latency_ms = int(raw.get("latency_ms", 0))
    latency_jitter_pct = float(raw.get("latency_jitter_pct", 0.0))
    fault_profile = dict(raw.get("faults", {}))
    random_seed = raw.get("random_seed")

    transport_cfg = raw.get("transport")
    transport = None
    if transport_cfg:
        transport = TransportConfig(
            tcp_host=transport_cfg.get("tcp_host"),
            tcp_port=transport_cfg.get("tcp_port"),
            serial_port=transport_cfg.get("serial_port"),
            serial_baud=int(transport_cfg.get("serial_baud", 9600)),
        )
        transport.validate()

    return MockServerConfig(
        unit_id=int(raw.get("unit_id", 1)),
        groups=groups,
        default_rules=default_rules,
        value_scripts=value_scripts,
        latency_ms=latency_ms,
        latency_jitter_pct=latency_jitter_pct,
        fault_profile=fault_profile,
        transport=transport,
        random_seed=int(random_seed) if random_seed is not None else None,
    )
