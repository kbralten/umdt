"""Prober core implementation.

This module provides an async-friendly Prober that probes connection
parameter combinations and reports endpoints that respond for a
configured target register.

The implementation uses the same compatibility layer as the CLI/GUI
so behavior is consistent across tools.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple, Union
from urllib.parse import urlparse, parse_qs

from umdt.core.data_types import DATA_TYPE_PROPERTIES, DataType, is_bit_type
from umdt.utils.modbus_compat import (
    create_client,
    close_client,
    read_holding_registers,
    read_input_registers,
    read_coils,
    read_discrete_inputs,
)

DEFAULT_TIMEOUT_MS = 100


@dataclass
class TargetSpec:
    datatype: DataType
    address: int
    expected_value: Optional[Any] = None


@dataclass
class ProbeResult:
    uri: str
    params: Dict[str, Any]
    alive: bool
    response_summary: Optional[str]
    elapsed_ms: float


class Prober:
    """Async Prober for connection parameter discovery.

    transport_factory is optional and kept for future extension; current
    implementation builds ephemeral pymodbus sync clients on each probe
    and executes them in a thread via `asyncio.to_thread`.
    """

    def __init__(
        self,
        transport_factory: Optional[Callable[..., Any]] = None,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        concurrency: int = 64,
        attempts: int = 1,
        backoff_ms: int = 0,
        logger: Optional[Any] = None,
    ) -> None:
        self.transport_factory = transport_factory
        self.timeout_ms = int(timeout_ms)
        self.concurrency = max(1, int(concurrency))
        self.attempts = max(1, int(attempts))
        self.backoff_ms = int(backoff_ms)
        self.logger = logger

    async def run(
        self,
        combinations: Iterable[Union[str, Dict[str, Any]]],
        target: TargetSpec,
        on_result: Optional[Callable[[ProbeResult], None]] = None,
        cancel_token: Optional[asyncio.Event] = None,
    ) -> List[ProbeResult]:
        """Run probes over the provided combinations.

        combinations may be strings (canonical URIs) or dicts describing
        transport parameters (e.g. {'host':..., 'port':..., 'unit':...} or
        {'serial': '/dev/ttyS1','baud':9600,'unit':1}).
        
        Serial combos are probed sequentially to avoid port conflicts; TCP combos
        are probed concurrently.
        """
        results: List[ProbeResult] = []
        
        # Separate serial and TCP combinations
        serial_combos = []
        tcp_combos = []
        
        for combo in combinations:
            if cancel_token and cancel_token.is_set():
                break
            uri, _ = self._normalize_combo_to_uri(combo)
            parsed = urlparse(uri)
            scheme = parsed.scheme or 'serial'
            if scheme == 'serial' or (isinstance(combo, dict) and 'serial' in combo):
                serial_combos.append(combo)
            else:
                tcp_combos.append(combo)
        
        # Probe serial combinations sequentially (concurrency=1)
        if serial_combos:
            for combo in serial_combos:
                if cancel_token and cancel_token.is_set():
                    break
                pr = await self._probe_single(combo, target, cancel_token)
                results.append(pr)
                if on_result:
                    try:
                        on_result(pr)
                    except Exception:
                        pass
        
        # Probe TCP combinations concurrently
        if tcp_combos:
            sem = asyncio.Semaphore(self.concurrency)
            results_lock = asyncio.Lock()
            tasks: List[asyncio.Task] = []

            async def _probe_wrapper(combo: Union[str, Dict[str, Any]]):
                async with sem:
                    if cancel_token and cancel_token.is_set():
                        return
                    
                    pr = await self._probe_single(combo, target, cancel_token)
                    async with results_lock:
                        results.append(pr)
                    
                    if on_result:
                        try:
                            on_result(pr)
                        except Exception:
                            pass

            for combo in tcp_combos:
                if cancel_token and cancel_token.is_set():
                    break
                task = asyncio.create_task(_probe_wrapper(combo))
                tasks.append(task)
            
            if tasks:
                await asyncio.gather(*tasks)

        return results
    
    async def _probe_single(
        self,
        combo: Union[str, Dict[str, Any]],
        target: TargetSpec,
        cancel_token: Optional[asyncio.Event] = None,
    ) -> ProbeResult:
        """Probe a single combination and return ProbeResult."""
        uri, params = self._normalize_combo_to_uri(combo)
        start = time.perf_counter()
        alive = False
        resp_summary: Optional[str] = None

        # Try attempts with optional backoff
        for attempt in range(self.attempts):
            if cancel_token and cancel_token.is_set():
                break
            try:
                # execute blocking probe in thread
                # Note: We rely on the pymodbus client timeout, not asyncio.wait_for,
                # because wait_for starts counting before the thread is scheduled,
                # which can cause false timeouts under high concurrency.
                timeout_s = max(0.001, self.timeout_ms / 1000.0)
                probe_ok, summary = await asyncio.to_thread(
                    self._blocking_probe, uri, target, params, timeout_s
                )
                alive = probe_ok
                resp_summary = summary
                if alive:
                    break
            except asyncio.TimeoutError:
                # treat timeout as failed attempt; continue to next attempt
                resp_summary = "timeout"
            except Exception as exc:  # pragma: no cover - defensive
                resp_summary = f"error: {exc}"
            if not alive and self.backoff_ms:
                await asyncio.sleep(self.backoff_ms / 1000.0)

        elapsed = (time.perf_counter() - start) * 1000.0
        return ProbeResult(uri=uri, params=params, alive=alive, response_summary=resp_summary, elapsed_ms=elapsed)

    def _normalize_combo_to_uri(self, combo: Union[str, Dict[str, Any]]) -> Tuple[str, Dict[str, Any]]:
        """Convert combo to a canonical URI and params dict.

        Supported combo shapes:
          - string: already a URI (returned unchanged)
          - dict with keys 'host' and 'port' and optional 'unit' -> tcp://host:port?unit=X
          - dict with keys 'serial' and 'baud' and optional 'unit' -> serial://PORT:BAUD?unit=X
        """
        if isinstance(combo, str):
            return combo, {}
        params: Dict[str, Any] = dict(combo)
        if 'host' in combo and 'port' in combo:
            host = combo.get('host')
            port = combo.get('port')
            unit = combo.get('unit')
            uri = f"tcp://{host}:{port}"
            if unit is not None:
                uri = f"{uri}?unit={unit}"
            return uri, params
        if 'serial' in combo:
            port = combo.get('serial')
            baud = combo.get('baud')
            unit = combo.get('unit')
            uri = f"serial://{port}:{baud}"
            if unit is not None:
                uri = f"{uri}?unit={unit}"
            return uri, params
        # Fallback: stringify the dict
        return (str(combo), params)

    def _blocking_probe(self, uri: str, target: TargetSpec, params: Dict[str, Any], timeout_s: float) -> Tuple[bool, Optional[str]]:
        """Blocking probe implementation executed in a thread.

        Returns (alive: bool, summary: Optional[str]).
        """
        parsed = urlparse(uri)
        scheme = parsed.scheme or 'serial'
        qs = parse_qs(parsed.query or "")
        unit = None
        if qs:
            try:
                unit = int(qs.get('unit', [None])[0]) if qs.get('unit') else None
            except Exception:
                unit = None
        # allow override from params
        unit = params.get('unit', unit) or 1

        client = None
        try:
            if scheme == 'serial':
                # parsed.netloc may be empty; use path for windows-style 'serial://COM3:9600'
                netloc = parsed.netloc or parsed.path.lstrip('/')
                port = None
                baud = None
                if ':' in netloc:
                    port, baud_s = netloc.split(':', 1)
                    try:
                        baud = int(baud_s)
                    except Exception:
                        baud = params.get('baud')
                else:
                    port = netloc or params.get('serial')
                    baud = params.get('baud')
                # Create a compat client and disable retries for serial probes
                try:
                    client = create_client(kind='serial', serial_port=port, baudrate=baud, timeout=timeout_s, retries=0)
                except Exception:
                    client = create_client(kind='serial', serial_port=port, baudrate=baud)
            else:
                host = parsed.hostname or params.get('host') or '127.0.0.1'
                tcp_port = parsed.port or int(params.get('port', 502))
                try:
                    client = create_client(kind='tcp', host=host, port=tcp_port, timeout=timeout_s)
                except Exception:
                    client = create_client(kind='tcp', host=host, port=tcp_port)

            # Connect
            try:
                connected = client.connect()
            except Exception as e:
                # Ensure client is closed on connection error
                try:
                    if client:
                        close_client(client)
                except Exception:
                    pass
                return False, f"connect-error: {e}"
            if not connected:
                # Ensure client is closed on connection failure
                try:
                    if client:
                        close_client(client)
                except Exception:
                    pass
                return False, "connect-failed"

            props = DATA_TYPE_PROPERTIES[target.datatype]
            if not props.readable or not props.pymodbus_read_method:
                client.close()
                return False, "datatype-not-readable"

            regs_to_read = 1 if not is_bit_type(target.datatype) else 1

            try:
                _read_map = {
                    'read_holding_registers': read_holding_registers,
                    'read_input_registers': read_input_registers,
                    'read_coils': read_coils,
                    'read_discrete_inputs': read_discrete_inputs,
                }
                reader = _read_map.get(props.pymodbus_read_method)
                if reader:
                    rr = reader(client, target.address, regs_to_read, unit)
                else:
                    from umdt.utils.modbus_compat import invoke_method
                    rr = invoke_method(client, props.pymodbus_read_method, target.address, regs_to_read, unit)
            except Exception as e:
                try:
                    close_client(client)
                except Exception:
                    pass
                return False, f"read-error: {e}"

            try:
                close_client(client)
            except Exception:
                pass

            # Consider any non-None, non-isError response as success; also accept protocol exceptions
            # except for gateway errors (10, 11) which indicate device unreachable
            if rr is None:
                return False, "no-response"
            try:
                if hasattr(rr, 'isError') and rr.isError():
                    # Check for gateway/device unreachable errors (exception codes 10, 11)
                    exc_code = None
                    if hasattr(rr, 'exception_code'):
                        try:
                            exc_code = int(rr.exception_code)
                        except Exception:
                            pass
                    
                    if exc_code in (10, 11):
                        # Gateway path unavailable (10) or target device failed to respond (11)
                        return False, f"gateway-error:code-{exc_code}"
                    
                    # Other protocol exceptions count as alive (device responding, just wrong request)
                    return True, f"exception:{type(rr).__name__}:code-{exc_code if exc_code else 'unknown'}"
            except Exception:
                pass
            # Otherwise treat as data response
            return True, f"response:{str(rr)}"

        except Exception as exc:  # pragma: no cover - defensive
            try:
                if client:
                    client.close()
            except Exception:
                pass
            return False, f"probe-exception:{exc}"
