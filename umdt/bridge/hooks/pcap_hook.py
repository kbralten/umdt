"""PCAP logging hook for the Bridge pipeline.

This hook captures all Modbus traffic passing through the bridge and writes it
to a PCAP file for forensic analysis in Wireshark or similar tools.

Usage:
    from umdt.bridge.hooks.pcap_hook import PcapHook

    hook = PcapHook("bridge_capture.pcap")
    await hook.start()

    bridge.pipeline.add_ingress_hook(hook.ingress_hook)
    bridge.pipeline.add_response_hook(hook.response_hook)

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
    """PCAP logging hook for capturing bridge traffic.

    This hook provides:
      - ingress_hook: Captures requests from upstream (direction=INBOUND)
      - response_hook: Captures responses from downstream (direction=OUTBOUND)

    The PCAP file uses DLT_USER0 linktype with a 4-byte metadata header
    indicating direction and protocol type (RTU/TCP).
    """

    def __init__(
        self,
        filepath: Union[str, Path],
        log_raw_frames: bool = True,
    ):
        """Initialize the PCAP hook.

        Args:
            filepath: Path to the output .pcap file
            log_raw_frames: If True, log the raw wire format; if False, log PDU only
        """
        self.filepath = Path(filepath)
        self.log_raw_frames = log_raw_frames
        self._writer: Optional[PcapWriter] = None
        self._started = False

    async def start(self) -> None:
        """Open the PCAP file and start logging."""
        if self._started:
            return

        self._writer = PcapWriter(self.filepath)
        self._writer.open()
        self._started = True
        logger.info("PCAP logging started: %s", self.filepath)

    async def stop(self) -> None:
        """Flush and close the PCAP file."""
        if not self._started or not self._writer:
            return

        await self._writer.flush_async()
        await self._writer.aclose()
        logger.info(
            "PCAP logging stopped: %d packets, %d bytes",
            self._writer.packet_count,
            self._writer.bytes_written,
        )
        self._started = False
        self._writer = None

    def _get_protocol_hint(self, frame_type: FrameType) -> int:
        """Map FrameType to PCAP protocol hint."""
        if frame_type == FrameType.RTU:
            return PcapWriter.PROTO_MODBUS_RTU
        elif frame_type == FrameType.TCP:
            return PcapWriter.PROTO_MODBUS_TCP
        return PcapWriter.PROTO_UNKNOWN

    async def ingress_hook(
        self,
        request: Request,
        context: HookContext,
    ) -> Optional[Request]:
        """Capture inbound requests to PCAP.

        This hook is registered with pipeline.add_ingress_hook().
        """
        if not self._started or not self._writer:
            return request

        # Determine what to log
        if self.log_raw_frames:
            data = request.raw_frame
            protocol = self._get_protocol_hint(request.source_frame_type)
        else:
            # Log just the PDU (function code + data)
            data = bytes([request.function_code]) + request.data
            protocol = PcapWriter.PROTO_UNKNOWN

        await self._writer.write_packet_async(
            data=data,
            direction=Direction.INBOUND,
            protocol=protocol,
            timestamp=request.timestamp,
        )

        # Pass through unchanged
        return request

    async def response_hook(
        self,
        response: Response,
        context: HookContext,
    ) -> Optional[Response]:
        """Capture outbound responses to PCAP.

        This hook is registered with pipeline.add_response_hook().
        """
        if not self._started or not self._writer:
            return response

        # Determine what to log
        if self.log_raw_frames:
            data = response.raw_frame
            protocol = self._get_protocol_hint(response.source_frame_type)
        else:
            # Log just the PDU
            data = bytes([response.function_code]) + response.pdu.data
            protocol = PcapWriter.PROTO_UNKNOWN

        await self._writer.write_packet_async(
            data=data,
            direction=Direction.OUTBOUND,
            protocol=protocol,
            timestamp=response.timestamp,
        )

        # Pass through unchanged
        return response

    @property
    def is_active(self) -> bool:
        """Check if PCAP logging is currently active."""
        return self._started

    @property
    def stats(self) -> dict:
        """Get logging statistics."""
        if not self._writer:
            return {"packets": 0, "bytes": 0, "active": False}
        return {
            "packets": self._writer.packet_count,
            "bytes": self._writer.bytes_written,
            "active": self._started,
        }
