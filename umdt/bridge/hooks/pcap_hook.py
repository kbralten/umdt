"""PCAP logging hook for the Bridge pipeline.

This hook captures Modbus traffic passing through the bridge and writes it
to PCAP file(s) for forensic analysis in Wireshark or similar tools.

Supports dual-stream logging per architecture spec (Section 2.4):
  - Upstream PCAP: Complete conversation between Master (SCADA) and Bridge
      - Master → Bridge (requests)
      - Bridge → Master (responses)
  - Downstream PCAP: Complete conversation between Bridge and Slave (PLC)
      - Bridge → Slave (requests, after any transformations)
      - Slave → Bridge (responses)

This allows engineers to see "what goes in" versus "what comes out" to
diagnose issues like transformation hooks modifying registers.

Usage:
    # Single combined PCAP (legacy mode)
    hook = PcapHook(combined="bridge_capture.pcap")

    # Dual-stream PCAP (recommended for debugging transformations)
    hook = PcapHook(
        upstream="upstream.pcap",    # Master <-> Bridge traffic
        downstream="downstream.pcap"  # Bridge <-> Slave traffic
    )

    await hook.start()
    bridge.pipeline.add_ingress_hook(hook.ingress_hook)
    bridge.pipeline.add_egress_hook(hook.egress_hook)
    bridge.pipeline.add_response_hook(hook.response_hook)
    bridge.pipeline.add_response_hook(hook.upstream_response_hook)
    # ... run bridge ...
    await hook.stop()
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Union

from umdt.core.pcap import Direction, PcapWriter

# Import pipeline types for type hints
from ..pipeline import HookContext, Request, Response
from ..protocol import FrameType

logger = logging.getLogger("umdt.bridge.hooks.pcap")


class PcapHook:
    """PCAP logging hook for capturing bridge traffic with dual-stream support.

    Dual-stream mode logs complete conversations to separate files:
      - upstream.pcap: Master <-> Bridge (what SCADA sees)
      - downstream.pcap: Bridge <-> Slave (what device sees)

    Traffic flow and logging:
      1. Master sends request → ingress_hook logs to UPSTREAM
      2. Bridge (optionally transforms) sends to Slave → egress_hook logs to DOWNSTREAM  
      3. Slave responds → response_hook logs to DOWNSTREAM
      4. Bridge relays to Master → upstream_response_hook logs to UPSTREAM

    The PCAP files use DLT_USER0 linktype with a 4-byte metadata header
    indicating direction and protocol type (RTU/TCP).
    """

    def __init__(
        self,
        combined: Optional[Union[str, Path]] = None,
        upstream: Optional[Union[str, Path]] = None,
        downstream: Optional[Union[str, Path]] = None,
        log_raw_frames: bool = True,
    ):
        """Initialize the PCAP hook.

        Args:
            combined: Path to single combined PCAP file (legacy mode)
            upstream: Path to upstream PCAP (Master <-> Bridge traffic)
            downstream: Path to downstream PCAP (Bridge <-> Slave traffic)
            log_raw_frames: If True, log the raw wire format; if False, log PDU only

        Note:
            If both combined and upstream/downstream are specified, all files
            will be written to. For typical usage, specify either combined OR
            upstream+downstream.
        """
        self.combined_path = Path(combined) if combined else None
        self.upstream_path = Path(upstream) if upstream else None
        self.downstream_path = Path(downstream) if downstream else None
        self.log_raw_frames = log_raw_frames

        self._combined_writer: Optional[PcapWriter] = None
        self._upstream_writer: Optional[PcapWriter] = None
        self._downstream_writer: Optional[PcapWriter] = None
        self._started = False

    async def start(self) -> None:
        """Open the PCAP file(s) and start logging."""
        if self._started:
            return

        if self.combined_path:
            self._combined_writer = PcapWriter(self.combined_path)
            self._combined_writer.open()
            logger.info("PCAP logging (combined): %s", self.combined_path)

        if self.upstream_path:
            self._upstream_writer = PcapWriter(self.upstream_path)
            self._upstream_writer.open()
            logger.info("PCAP logging (upstream): %s", self.upstream_path)

        if self.downstream_path:
            self._downstream_writer = PcapWriter(self.downstream_path)
            self._downstream_writer.open()
            logger.info("PCAP logging (downstream): %s", self.downstream_path)

        self._started = True

    async def stop(self) -> None:
        """Flush and close all PCAP file(s)."""
        if not self._started:
            return

        for name, writer in [
            ("combined", self._combined_writer),
            ("upstream", self._upstream_writer),
            ("downstream", self._downstream_writer),
        ]:
            if writer:
                await writer.flush_async()
                await writer.aclose()
                logger.info(
                    "PCAP logging stopped (%s): %d packets, %d bytes",
                    name,
                    writer.packet_count,
                    writer.bytes_written,
                )

        self._combined_writer = None
        self._upstream_writer = None
        self._downstream_writer = None
        self._started = False

    def _get_protocol_hint(self, frame_type: FrameType) -> int:
        """Map FrameType to PCAP protocol hint."""
        if frame_type == FrameType.RTU:
            return PcapWriter.PROTO_MODBUS_RTU
        elif frame_type == FrameType.TCP:
            return PcapWriter.PROTO_MODBUS_TCP
        return PcapWriter.PROTO_UNKNOWN

    async def _write_to_upstream(
        self,
        data: bytes,
        direction: Direction,
        protocol: int,
        timestamp: Optional[float] = None,
    ) -> None:
        """Write a packet to upstream PCAP (and combined if enabled)."""
        if self._upstream_writer:
            await self._upstream_writer.write_packet_async(
                data=data,
                direction=direction,
                protocol=protocol,
                timestamp=timestamp,
            )
        if self._combined_writer:
            await self._combined_writer.write_packet_async(
                data=data,
                direction=direction,
                protocol=protocol,
                timestamp=timestamp,
            )

    async def _write_to_downstream(
        self,
        data: bytes,
        direction: Direction,
        protocol: int,
        timestamp: Optional[float] = None,
    ) -> None:
        """Write a packet to downstream PCAP (and combined if enabled)."""
        if self._downstream_writer:
            await self._downstream_writer.write_packet_async(
                data=data,
                direction=direction,
                protocol=protocol,
                timestamp=timestamp,
            )
        if self._combined_writer:
            await self._combined_writer.write_packet_async(
                data=data,
                direction=direction,
                protocol=protocol,
                timestamp=timestamp,
            )

    async def ingress_hook(
        self,
        request: Request,
        context: HookContext,
    ) -> Optional[Request]:
        """Capture inbound requests from upstream (Master -> Bridge).

        This captures what the SCADA *thinks* it is asking for BEFORE
        any Logic Engine transformations.

        Logged to: UPSTREAM PCAP (direction: INBOUND)

        This hook is registered with pipeline.add_ingress_hook().
        """
        if not self._started:
            return request

        # Determine what to log
        if self.log_raw_frames:
            data = request.raw_frame
            protocol = self._get_protocol_hint(request.source_frame_type)
        else:
            # Log just the PDU (function code + data)
            data = bytes([request.function_code]) + request.data
            protocol = PcapWriter.PROTO_UNKNOWN

        # Log to UPSTREAM pcap: Master -> Bridge
        await self._write_to_upstream(
            data=data,
            direction=Direction.INBOUND,
            protocol=protocol,
            timestamp=request.timestamp,
        )

        # Pass through unchanged
        return request

    async def egress_hook(
        self,
        request: Request,
        context: HookContext,
    ) -> Optional[Request]:
        """Capture outbound requests to downstream (Bridge -> Slave).

        This captures what was *actually sent* to the wire AFTER any
        Logic Engine transformations.

        Logged to: DOWNSTREAM PCAP (direction: OUTBOUND)

        This hook is registered with pipeline.add_egress_hook().
        """
        if not self._started:
            return request

        # Determine what to log
        if self.log_raw_frames:
            data = request.raw_frame
            protocol = self._get_protocol_hint(request.source_frame_type)
        else:
            data = bytes([request.function_code]) + request.data
            protocol = PcapWriter.PROTO_UNKNOWN

        # Log to DOWNSTREAM pcap: Bridge -> Slave
        await self._write_to_downstream(
            data=data,
            direction=Direction.OUTBOUND,
            protocol=protocol,
            timestamp=request.timestamp,
        )

        return request

    async def response_hook(
        self,
        response: Response,
        context: HookContext,
    ) -> Optional[Response]:
        """Capture responses from downstream (Slave -> Bridge).

        This captures the response from the slave device.

        Logged to: DOWNSTREAM PCAP (direction: INBOUND)

        This hook is registered with pipeline.add_response_hook().
        """
        if not self._started:
            return response

        # Determine what to log
        if self.log_raw_frames:
            data = response.raw_frame
            protocol = self._get_protocol_hint(response.source_frame_type)
        else:
            # Log just the PDU
            data = bytes([response.function_code]) + response.pdu.data
            protocol = PcapWriter.PROTO_UNKNOWN

        # Log to DOWNSTREAM pcap: Slave -> Bridge
        await self._write_to_downstream(
            data=data,
            direction=Direction.INBOUND,
            protocol=protocol,
            timestamp=response.timestamp,
        )

        # Pass through unchanged
        return response

    async def upstream_response_hook(
        self,
        response: Response,
        context: HookContext,
    ) -> Optional[Response]:
        """Capture responses sent to upstream (Bridge -> Master).

        This captures the response being relayed back to the master.

        Logged to: UPSTREAM PCAP (direction: OUTBOUND)

        This hook should be registered AFTER response_hook to capture
        the response as it's sent back to the master.
        """
        if not self._started:
            return response

        # Determine what to log
        if self.log_raw_frames:
            data = response.raw_frame
            protocol = self._get_protocol_hint(response.source_frame_type)
        else:
            data = bytes([response.function_code]) + response.pdu.data
            protocol = PcapWriter.PROTO_UNKNOWN

        # Log to UPSTREAM pcap: Bridge -> Master
        await self._write_to_upstream(
            data=data,
            direction=Direction.OUTBOUND,
            protocol=protocol,
            timestamp=response.timestamp,
        )

        return response

    @property
    def is_active(self) -> bool:
        """Check if PCAP logging is currently active."""
        return self._started

    @property
    def stats(self) -> dict:
        """Get logging statistics for all streams."""
        result = {"active": self._started}

        if self._combined_writer:
            result["combined"] = {
                "packets": self._combined_writer.packet_count,
                "bytes": self._combined_writer.bytes_written,
            }

        if self._upstream_writer:
            result["upstream"] = {
                "packets": self._upstream_writer.packet_count,
                "bytes": self._upstream_writer.bytes_written,
            }

        if self._downstream_writer:
            result["downstream"] = {
                "packets": self._downstream_writer.packet_count,
                "bytes": self._downstream_writer.bytes_written,
            }

        # For backward compatibility, provide total packets
        total_packets = 0
        total_bytes = 0
        for key in ["combined", "upstream", "downstream"]:
            if key in result:
                total_packets += result[key]["packets"]
                total_bytes += result[key]["bytes"]

        result["packets"] = total_packets
        result["bytes"] = total_bytes

        return result
