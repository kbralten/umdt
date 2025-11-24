from __future__ import annotations

import asyncio
from array import array
import math
import time
from typing import Dict, Iterable, List, Optional

from umdt.core.data_types import DataType, is_register_type

from .config import MockServerConfig
from .diagnostics import DiagnosticsManager
from .models import RegisterGroup, RegisterRule, ResponseMode, ValueScript


class RequestDropped(Exception):
    """Raised when diagnostics suppresses a response entirely."""
    pass


class RegisterAccessError(Exception):
    """Raised when a register rule forces an exception response."""

    def __init__(self, code: int) -> None:
        self.code = code
        super().__init__(f"Forced Modbus exception 0x{code:02X}")


class MockDevice:
    """In-memory Modbus slave with diagnostics controls."""

    def __init__(self, config: MockServerConfig) -> None:
        self._config = config
        self._lock = asyncio.Lock()
        self._groups: Dict[DataType, List[RegisterGroup]] = {}
        self._rules: Dict[int, RegisterRule] = {}
        self._scripts: Dict[int, ValueScript] = config.value_scripts
        self._registers: Dict[DataType, array] = {}
        self._bits: Dict[DataType, List[bool]] = {}
        self.diagnostics = DiagnosticsManager()
        if config.fault_profile:
            self.diagnostics.configure(config.fault_profile)
        if config.random_seed is not None:
            self.diagnostics.update(random_seed=config.random_seed)
        self._init_storage()

    def _init_storage(self) -> None:
        for group in self._config.groups:
            self._groups.setdefault(group.data_type, []).append(group)
            if is_register_type(group.data_type):
                store = self._registers.setdefault(group.data_type, array('H'))
                store.extend([0] * group.length)
            else:
                bit_store = self._bits.setdefault(group.data_type, [])
                bit_store.extend([False] * group.length)

        for key, rule in self._config.default_rules.items():
            try:
                addr = int(key)
            except ValueError:
                continue
            self._rules[addr] = rule

    def groups_for(self, dtype: DataType) -> List[RegisterGroup]:
        return self._groups.get(dtype, [])

    async def read(self, dtype: DataType, address: int, count: int) -> List[int | bool]:
        await self.diagnostics.maybe_apply_latency()
        if self.diagnostics.should_drop_request():
            await self.diagnostics.emit("read", f"Dropped read request type={dtype.value}")
            raise RequestDropped()
        if is_register_type(dtype):
            return await self._read_registers(dtype, address, count)
        return await self._read_bits(dtype, address, count)

    async def write(self, dtype: DataType, address: int, values: Iterable[int | bool]) -> None:
        await self.diagnostics.maybe_apply_latency()
        if self.diagnostics.should_drop_request():
            await self.diagnostics.emit("write", f"Dropped write request type={dtype.value}")
            raise RequestDropped()
        if is_register_type(dtype):
            await self._write_registers(dtype, address, list(int(v) for v in values))
        else:
            await self._write_bits(dtype, address, [bool(v) for v in values])

    async def _read_registers(self, dtype: DataType, address: int, count: int) -> List[int]:
        async with self._lock:
            store = self._registers.get(dtype)
            if store is None:
                raise ValueError(f"No register storage for {dtype.value}")
            results: List[int] = []
            for offset in range(count):
                idx = self._resolve_index(dtype, address + offset)
                absolute = address + offset
                value = int(store[idx])
                rule = self._rules.get(absolute)
                if rule and rule.response_mode == ResponseMode.EXCEPTION:
                    raise RegisterAccessError(rule.exception_code or 0x02)
                if rule and rule.response_mode == ResponseMode.FROZEN_VALUE and rule.forced_value is not None:
                    value = int(rule.forced_value) & 0xFFFF
                value = self._apply_script(address + offset, value)
                results.append(value)
            results = self.diagnostics.apply_bit_flips(results)
            return results

    async def _read_bits(self, dtype: DataType, address: int, count: int) -> List[int | bool]:
        async with self._lock:
            store = self._bits.get(dtype)
            if store is None:
                raise ValueError(f"No bit storage for {dtype.value}")
            results: List[int | bool] = []
            for offset in range(count):
                idx = self._resolve_index(dtype, address + offset)
                value = bool(store[idx])
                results.append(value)
            return results

    async def _write_registers(self, dtype: DataType, address: int, values: List[int]) -> None:
        async with self._lock:
            store = self._registers.get(dtype)
            if store is None:
                raise ValueError(f"No register storage for {dtype.value}")
            for offset, value in enumerate(values):
                idx = self._resolve_index(dtype, address + offset)
                absolute = address + offset
                rule = self._rules.get(absolute)
                if rule and rule.response_mode == ResponseMode.EXCEPTION:
                    raise RegisterAccessError(rule.exception_code or 0x02)
                if rule and (rule.ignore_write or rule.response_mode == ResponseMode.IGNORE_WRITE):
                    continue
                store[idx] = int(value) & 0xFFFF

    async def _write_bits(self, dtype: DataType, address: int, values: List[bool]) -> None:
        async with self._lock:
            store = self._bits.get(dtype)
            if store is None:
                raise ValueError(f"No bit storage for {dtype.value}")
            for offset, value in enumerate(values):
                idx = self._resolve_index(dtype, address + offset)
                absolute = address + offset
                rule = self._rules.get(absolute)
                if rule and rule.response_mode == ResponseMode.EXCEPTION:
                    raise RegisterAccessError(rule.exception_code or 0x02)
                if rule and (rule.ignore_write or rule.response_mode == ResponseMode.IGNORE_WRITE):
                    continue
                store[idx] = bool(value)

    def _resolve_index(self, dtype: DataType, address: int) -> int:
        groups = self._groups.get(dtype)
        if not groups:
            raise ValueError(f"No configured groups for {dtype.value}")
        offset = 0
        for group in groups:
            if group.contains(address):
                return offset + (address - group.start)
            offset += group.length
        raise ValueError(f"Address {address} not in any group for {dtype.value}")

    def snapshot(self) -> Dict[str, List[int | bool]]:
        snapshot: Dict[str, List[int | bool]] = {}
        for dtype, store in self._registers.items():
            snapshot[f"{dtype.value}_registers"] = list(store)
        for dtype, bits in self._bits.items():
            snapshot[f"{dtype.value}_bits"] = [bool(v) for v in bits]
        return snapshot

    async def apply_rule(self, address: int, rule: RegisterRule) -> None:
        async with self._lock:
            self._rules[address] = rule

    async def clear_rule(self, address: int) -> None:
        async with self._lock:
            self._rules.pop(address, None)

    def rules(self) -> Dict[int, RegisterRule]:
        return dict(self._rules)

    def _apply_script(self, address: int, value: int) -> int:
        script = self._scripts.get(address)
        if not script or not script.enabled or not script.expression:
            return value
        context = {
            "addr": address,
            "value": value,
            "timestamp": time.time(),
            "math": math,
        }
        try:
            result = eval(script.expression, {"__builtins__": {}}, context)  # noqa: S307 - intentional sandbox
        except Exception:
            return value
        try:
            return int(result) & 0xFFFF
        except Exception:
            return value
