from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional

from umdt.core.data_types import DataType


class ResponseMode(str, Enum):
    """How a register behaves when accessed."""

    NORMAL = "normal"
    EXCEPTION = "exception"
    IGNORE_WRITE = "ignore-write"
    FROZEN_VALUE = "frozen-value"


@dataclass(slots=True)
class RegisterGroup:
    """Logical grouping of sequential Modbus addresses."""

    name: str
    data_type: DataType
    start: int
    length: int
    writable: bool
    description: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def contains(self, address: int) -> bool:
        return self.start <= address < self.start + self.length

    def clamp(self, address: int) -> int:
        return max(self.start, min(address, self.start + self.length - 1))


@dataclass(slots=True)
class RegisterRule:
    """Policy overrides applied to a specific address."""

    response_mode: ResponseMode = ResponseMode.NORMAL
    forced_value: Optional[int] = None
    exception_code: Optional[int] = None
    ignore_write: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ValueScript:
    """Lightweight script descriptor evaluated at runtime."""

    expression: str
    description: str = ""
    enabled: bool = True