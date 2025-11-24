from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
import random
from typing import Any, Dict, List, Optional


@dataclass(slots=True)
class FaultInjectionSettings:
    """Runtime configurable knobs for fault injection."""

    latency_ms: int = 0
    latency_jitter_pct: float = 0.0
    drop_rate_pct: float = 0.0
    crc_corruption_pct: float = 0.0
    bit_flip_pct: float = 0.0
    enabled: bool = False
    random_seed: Optional[int] = None


@dataclass(slots=True)
class FaultEvent:
    """Event emitted whenever a simulated fault occurs."""

    timestamp: datetime
    transport: str
    description: str
    metadata: Dict[str, Any] = field(default_factory=dict)


class DiagnosticsManager:
    """Collects events and exposes the current fault profile."""

    def __init__(self) -> None:
        self._settings = FaultInjectionSettings()
        self._events: asyncio.Queue[FaultEvent] = asyncio.Queue()
        self._random = random.Random()

    @property
    def settings(self) -> FaultInjectionSettings:
        return self._settings

    def update(self, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            if hasattr(self._settings, key):
                setattr(self._settings, key, value)
        if "random_seed" in kwargs and kwargs["random_seed"] is not None:
            self._random.seed(int(kwargs["random_seed"]))

    def configure(self, profile: Dict[str, Any]) -> None:
        self.update(**profile)

    async def emit(self, transport: str, description: str, **metadata: Any) -> None:
        await self._events.put(
            FaultEvent(
                timestamp=datetime.now(timezone.utc),
                transport=transport,
                description=description,
                metadata=metadata,
            )
        )

    async def next_event(self) -> FaultEvent:
        return await self._events.get()

    def snapshot(self) -> Dict[str, Any]:
        return {
            "latency_ms": self._settings.latency_ms,
            "latency_jitter_pct": self._settings.latency_jitter_pct,
            "drop_rate_pct": self._settings.drop_rate_pct,
            "crc_corruption_pct": self._settings.crc_corruption_pct,
            "bit_flip_pct": self._settings.bit_flip_pct,
            "enabled": self._settings.enabled,
        }

    async def maybe_apply_latency(self) -> None:
        settings = self._settings
        if not settings.enabled or settings.latency_ms <= 0:
            return
        jitter = settings.latency_ms * (settings.latency_jitter_pct / 100.0)
        delta = (self._random.random() - 0.5) * 2 * jitter
        await asyncio.sleep(max(0.0, (settings.latency_ms + delta) / 1000.0))

    def should_drop_request(self) -> bool:
        settings = self._settings
        if not settings.enabled or settings.drop_rate_pct <= 0:
            return False
        return self._random.random() < (settings.drop_rate_pct / 100.0)

    def apply_bit_flips(self, registers: List[int]) -> List[int]:
        settings = self._settings
        if not settings.enabled or settings.bit_flip_pct <= 0:
            return registers
        result = []
        for value in registers:
            if self._random.random() < (settings.bit_flip_pct / 100.0):
                bit = 1 << self._random.randint(0, 15)
                result.append(value ^ bit)
            else:
                result.append(value)
        return result
