"""Bridge pipeline with hook architecture for extensibility.

The pipeline processes Modbus requests through a chain of hooks:
  1. Ingress Hook - when request arrives from upstream
  2. Transformation Hook - modify request before forwarding
  3. Egress Hook - before sending to downstream
  4. Response Hook - when downstream replies, before relaying upstream

This architecture supports future features like:
  - Script-based logic injection
  - MQTT telemetry sidecars
  - PCAP forensic logging
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union

from .protocol import FrameType, MBAPHeader, ModbusFrameParser, ModbusPDU

logger = logging.getLogger("umdt.bridge.pipeline")


@dataclass
class Request:
    """Represents a Modbus request in the pipeline."""
    unit_id: int
    pdu: ModbusPDU
    source_frame_type: FrameType
    raw_frame: bytes
    transaction_id: int = 0
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def function_code(self) -> int:
        return self.pdu.function_code

    @property
    def data(self) -> bytes:
        return self.pdu.data


@dataclass
class Response:
    """Represents a Modbus response in the pipeline."""
    unit_id: int
    pdu: ModbusPDU
    source_frame_type: FrameType
    raw_frame: bytes
    request: Optional[Request] = None
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def function_code(self) -> int:
        return self.pdu.function_code

    @property
    def is_exception(self) -> bool:
        return bool(self.pdu.function_code & 0x80)


@dataclass
class HookContext:
    """Context passed to hooks for state sharing."""
    state: Dict[str, Any] = field(default_factory=dict)
    upstream_type: FrameType = FrameType.TCP
    downstream_type: FrameType = FrameType.RTU
    bridge_started: float = field(default_factory=time.time)


# Hook type definitions
IngressHook = Callable[[Request, HookContext], Awaitable[Optional[Request]]]
TransformHook = Callable[[Request, HookContext], Awaitable[Optional[Request]]]
EgressHook = Callable[[Request, HookContext], Awaitable[Optional[Request]]]
ResponseHook = Callable[[Response, HookContext], Awaitable[Optional[Response]]]
PeriodicHook = Callable[[HookContext], Awaitable[None]]


class BridgePipeline:
    """Processes Modbus traffic through configurable hook chains.

    The pipeline supports:
      - Protocol conversion (TCP <-> RTU)
      - Request/response inspection and modification
      - Extensible hook architecture for future features
    """

    def __init__(
        self,
        upstream_type: FrameType = FrameType.TCP,
        downstream_type: FrameType = FrameType.RTU,
    ):
        self.upstream_type = upstream_type
        self.downstream_type = downstream_type
        self.context = HookContext(
            upstream_type=upstream_type,
            downstream_type=downstream_type,
        )

        # Hook chains
        self._ingress_hooks: List[IngressHook] = []
        self._transform_hooks: List[TransformHook] = []
        self._egress_hooks: List[EgressHook] = []
        self._response_hooks: List[ResponseHook] = []
        self._periodic_hooks: List[tuple[PeriodicHook, float]] = []  # (hook, interval_ms)

        # Statistics
        self._stats = {
            "requests_processed": 0,
            "responses_processed": 0,
            "requests_blocked": 0,
            "errors": 0,
        }

    # --- Hook Registration ---

    def add_ingress_hook(self, hook: IngressHook) -> None:
        """Add hook called when request arrives from upstream."""
        self._ingress_hooks.append(hook)

    def add_transform_hook(self, hook: TransformHook) -> None:
        """Add hook to modify request before forwarding."""
        self._transform_hooks.append(hook)

    def add_egress_hook(self, hook: EgressHook) -> None:
        """Add hook called before sending to downstream."""
        self._egress_hooks.append(hook)

    def add_response_hook(self, hook: ResponseHook) -> None:
        """Add hook called when downstream responds."""
        self._response_hooks.append(hook)

    def add_periodic_hook(self, hook: PeriodicHook, interval_ms: float) -> None:
        """Add hook called on a timer (for future time-driven features)."""
        self._periodic_hooks.append((hook, interval_ms))

    # --- Request Processing ---

    async def process_request(self, raw_frame: bytes) -> Optional[bytes]:
        """Process incoming request from upstream, return frame for downstream.

        Returns None if the request should be blocked.
        """
        try:
            # Parse incoming frame based on upstream type
            request = self._parse_upstream_request(raw_frame)
            if request is None:
                return None

            logger.debug(
                "Processing request: unit=%d fc=%d data_len=%d",
                request.unit_id,
                request.function_code,
                len(request.data),
            )

            # Run through hook chains
            request = await self._run_ingress_hooks(request)
            if request is None:
                self._stats["requests_blocked"] += 1
                return None

            request = await self._run_transform_hooks(request)
            if request is None:
                self._stats["requests_blocked"] += 1
                return None

            request = await self._run_egress_hooks(request)
            if request is None:
                self._stats["requests_blocked"] += 1
                return None

            # Convert to downstream format
            downstream_frame = self._build_downstream_frame(request)
            self._stats["requests_processed"] += 1

            # Store request for response correlation
            self.context.state["last_request"] = request

            return downstream_frame

        except Exception as e:
            logger.exception("Error processing request: %s", e)
            self._stats["errors"] += 1
            return None

    async def process_response(self, raw_frame: bytes) -> Optional[bytes]:
        """Process response from downstream, return frame for upstream.

        Returns None if the response should be blocked.
        """
        try:
            # Parse response based on downstream type
            response = self._parse_downstream_response(raw_frame)
            if response is None:
                return None

            # Attach original request if available
            response.request = self.context.state.get("last_request")

            logger.debug(
                "Processing response: unit=%d fc=%d is_exception=%s",
                response.unit_id,
                response.function_code,
                response.is_exception,
            )

            # Run response hooks
            response = await self._run_response_hooks(response)
            if response is None:
                return None

            # Convert to upstream format
            upstream_frame = self._build_upstream_frame(response)
            self._stats["responses_processed"] += 1

            return upstream_frame

        except Exception as e:
            logger.exception("Error processing response: %s", e)
            self._stats["errors"] += 1
            return None

    # --- Frame Parsing ---

    def _parse_upstream_request(self, raw_frame: bytes) -> Optional[Request]:
        """Parse request from upstream format."""
        try:
            if self.upstream_type == FrameType.TCP:
                header, pdu = ModbusFrameParser.parse_tcp_frame(raw_frame)
                return Request(
                    unit_id=header.unit_id,
                    pdu=pdu,
                    source_frame_type=FrameType.TCP,
                    raw_frame=raw_frame,
                    transaction_id=header.transaction_id,
                )
            else:
                unit_id, pdu = ModbusFrameParser.parse_rtu_frame(raw_frame)
                return Request(
                    unit_id=unit_id,
                    pdu=pdu,
                    source_frame_type=FrameType.RTU,
                    raw_frame=raw_frame,
                )
        except ValueError as e:
            logger.warning("Failed to parse upstream request: %s", e)
            return None

    def _parse_downstream_response(self, raw_frame: bytes) -> Optional[Response]:
        """Parse response from downstream format."""
        try:
            if self.downstream_type == FrameType.TCP:
                header, pdu = ModbusFrameParser.parse_tcp_frame(raw_frame)
                return Response(
                    unit_id=header.unit_id,
                    pdu=pdu,
                    source_frame_type=FrameType.TCP,
                    raw_frame=raw_frame,
                )
            else:
                unit_id, pdu = ModbusFrameParser.parse_rtu_frame(raw_frame)
                return Response(
                    unit_id=unit_id,
                    pdu=pdu,
                    source_frame_type=FrameType.RTU,
                    raw_frame=raw_frame,
                )
        except ValueError as e:
            logger.warning("Failed to parse downstream response: %s", e)
            return None

    # --- Frame Building ---

    def _build_downstream_frame(self, request: Request) -> bytes:
        """Build frame for downstream in the target format."""
        if self.downstream_type == FrameType.RTU:
            return ModbusFrameParser.build_rtu_frame(request.unit_id, request.pdu)
        else:
            return ModbusFrameParser.build_tcp_frame(
                request.unit_id,
                request.pdu,
                request.transaction_id,
            )

    def _build_upstream_frame(self, response: Response) -> bytes:
        """Build frame for upstream in the target format."""
        # Get transaction ID from original request if available
        transaction_id = 0
        if response.request:
            transaction_id = response.request.transaction_id

        if self.upstream_type == FrameType.TCP:
            return ModbusFrameParser.build_tcp_frame(
                response.unit_id,
                response.pdu,
                transaction_id,
            )
        else:
            return ModbusFrameParser.build_rtu_frame(response.unit_id, response.pdu)

    # --- Hook Execution ---

    async def _run_ingress_hooks(self, request: Request) -> Optional[Request]:
        for hook in self._ingress_hooks:
            result = await hook(request, self.context)
            if result is None:
                return None
            request = result
        return request

    async def _run_transform_hooks(self, request: Request) -> Optional[Request]:
        for hook in self._transform_hooks:
            result = await hook(request, self.context)
            if result is None:
                return None
            request = result
        return request

    async def _run_egress_hooks(self, request: Request) -> Optional[Request]:
        for hook in self._egress_hooks:
            result = await hook(request, self.context)
            if result is None:
                return None
            request = result
        return request

    async def _run_response_hooks(self, response: Response) -> Optional[Response]:
        for hook in self._response_hooks:
            result = await hook(response, self.context)
            if result is None:
                return None
            response = result
        return response

    # --- Statistics ---

    def get_stats(self) -> Dict[str, int]:
        return dict(self._stats)

    def reset_stats(self) -> None:
        for key in self._stats:
            self._stats[key] = 0
