"""Bridge orchestrator - coordinates upstream server, downstream client, and pipeline.

The Bridge class is the main entry point for creating a soft-gateway between
Modbus Masters and Slaves with protocol conversion and extensibility hooks.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import List, Optional, Union

from .downstream import DownstreamClient
from .hooks.script_hook import ScriptHook
from .pipeline import BridgePipeline
from .protocol import FrameType, ModbusFrameParser
from .upstream import ClientSession, UpstreamServer

logger = logging.getLogger("umdt.bridge")


class Bridge:
    """Soft-Gateway bridging Modbus Masters to Slaves with protocol conversion.

    The Bridge:
      - Accepts connections from Modbus Masters (SCADA/HMI) on the upstream side
      - Forwards requests to Modbus Slaves (PLC/Sensor) on the downstream side
      - Handles protocol conversion (TCP <-> RTU)
      - Provides extensibility via the hook-based pipeline architecture

    Example (TCP Master -> RTU Slave):
        bridge = Bridge(
            upstream_type=FrameType.TCP,
            upstream_port=502,
            downstream_type=FrameType.RTU,
            downstream_serial_port="COM3",
            downstream_baudrate=9600,
        )
        await bridge.start()
    """

    def __init__(
        self,
        # Upstream (server) configuration
        upstream_type: FrameType = FrameType.TCP,
        upstream_host: str = "0.0.0.0",
        upstream_port: int = 502,
        upstream_serial_port: Optional[str] = None,
        upstream_baudrate: int = 9600,
        # Downstream (client) configuration
        downstream_type: FrameType = FrameType.RTU,
        downstream_host: Optional[str] = None,
        downstream_port: int = 502,
        downstream_serial_port: Optional[str] = None,
        downstream_baudrate: int = 9600,
        # Options
        timeout: float = 2.0,
        # Script configuration
        scripts: Optional[List[Union[str, Path]]] = None,
    ):
        self.upstream_type = upstream_type
        self.downstream_type = downstream_type

        # Create components
        self._pipeline = BridgePipeline(
            upstream_type=upstream_type,
            downstream_type=downstream_type,
        )

        self._upstream = UpstreamServer(
            frame_type=upstream_type,
            host=upstream_host,
            port=upstream_port,
            serial_port=upstream_serial_port,
            baudrate=upstream_baudrate,
        )

        self._downstream = DownstreamClient(
            frame_type=downstream_type,
            host=downstream_host,
            port=downstream_port,
            serial_port=downstream_serial_port,
            baudrate=downstream_baudrate,
            timeout=timeout,
        )

        # Script hook for logic injection
        self._script_hook: Optional[ScriptHook] = None
        if scripts:
            self._setup_scripts(scripts)

        # Wire up the request handler
        self._upstream.set_request_handler(self._handle_request)

        self._running = False

    def _setup_scripts(self, scripts: List[Union[str, Path]]) -> None:
        """Load and configure script hooks."""
        self._script_hook = ScriptHook(name="bridge")

        for script_path in scripts:
            path = Path(script_path)
            if path.exists():
                logger.info("Loading script: %s", path)
                self._script_hook.load_script_file(path)
            else:
                logger.warning("Script file not found: %s", path)

        # Register hooks with pipeline
        if self._script_hook.engine.has_request_hooks():
            self._pipeline.add_ingress_hook(self._script_hook.ingress_hook)
            logger.info("Script request hooks registered")

        if self._script_hook.engine.has_response_hooks():
            self._pipeline.add_response_hook(self._script_hook.response_hook)
            logger.info("Script response hooks registered")

    async def start(self) -> None:
        """Start the bridge (upstream server and downstream client)."""
        logger.info("Starting bridge...")
        logger.info(
            "  Upstream: %s %s",
            self.upstream_type.name,
            self._describe_upstream(),
        )
        logger.info(
            "  Downstream: %s %s",
            self.downstream_type.name,
            self._describe_downstream(),
        )

        self._running = True

        # Connect to downstream first
        await self._downstream.connect()

        # Then start accepting upstream connections
        await self._upstream.start()

        logger.info("Bridge started successfully")

    async def stop(self) -> None:
        """Stop the bridge."""
        logger.info("Stopping bridge...")
        self._running = False
        await self._upstream.stop()
        await self._downstream.disconnect()
        logger.info("Bridge stopped")

    async def run_forever(self) -> None:
        """Run the bridge until interrupted."""
        await self.start()
        try:
            while self._running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()

    # --- Request Handling ---

    async def _handle_request(
        self,
        request_frame: bytes,
        client: ClientSession,
    ) -> Optional[bytes]:
        """Handle a request from upstream, forward to downstream, return response."""
        logger.debug(
            "Handling request from %s: %s",
            client.address,
            request_frame.hex().upper(),
        )

        # Process request through pipeline (may modify or block)
        downstream_frame = await self._pipeline.process_request(request_frame)
        if downstream_frame is None:
            # Check if script hook generated an exception
            if self._script_hook:
                exc = self._script_hook.get_pending_exception()
                if exc:
                    return self._build_exception_response(
                        request_frame,
                        exc["function_code"],
                        exc["code"],
                    )
            logger.debug("Request blocked by pipeline")
            return None

        # Forward to downstream and get response
        response_frame = await self._downstream.send_request(downstream_frame)
        if response_frame is None:
            logger.warning("No response from downstream")
            return None

        # Process response through pipeline (may modify)
        upstream_frame = await self._pipeline.process_response(response_frame)
        if upstream_frame is None:
            logger.debug("Response blocked by pipeline")
            return None

        return upstream_frame

    def _build_exception_response(
        self,
        request_frame: bytes,
        function_code: int,
        exception_code: int,
    ) -> bytes:
        """Build an exception response frame."""
        from .protocol import ModbusPDU

        exc_pdu = ModbusPDU(
            function_code=function_code | 0x80,
            data=bytes([exception_code]),
        )

        # Get transaction ID from request if TCP
        if self.upstream_type == FrameType.TCP:
            transaction_id = ModbusFrameParser.extract_mbap_transaction_id(request_frame)
            # Get unit_id from request
            if len(request_frame) >= 7:
                unit_id = request_frame[6]
            else:
                unit_id = 1
            return ModbusFrameParser.build_tcp_frame(unit_id, exc_pdu, transaction_id)
        else:
            # RTU - get unit_id from request
            unit_id = request_frame[0] if len(request_frame) >= 1 else 1
            return ModbusFrameParser.build_rtu_frame(unit_id, exc_pdu)

    # --- Pipeline Access ---

    @property
    def pipeline(self) -> BridgePipeline:
        """Access the pipeline for adding hooks."""
        return self._pipeline

    @property
    def script_hook(self) -> Optional[ScriptHook]:
        """Access the script hook if configured."""
        return self._script_hook

    def load_script(self, source: str, name: str = "inline") -> None:
        """Load a script from source code.
        
        Args:
            source: Python script source
            name: Identifier for this script
        """
        if not self._script_hook:
            self._script_hook = ScriptHook(name="bridge")
            # Register hooks with pipeline
            self._pipeline.add_ingress_hook(self._script_hook.ingress_hook)
            self._pipeline.add_response_hook(self._script_hook.response_hook)

        self._script_hook.load_script(source, name)
        logger.info("Loaded script: %s", name)

    def load_script_file(self, path: Union[str, Path]) -> None:
        """Load a script from a file.
        
        Args:
            path: Path to the script file
        """
        if not self._script_hook:
            self._script_hook = ScriptHook(name="bridge")
            self._pipeline.add_ingress_hook(self._script_hook.ingress_hook)
            self._pipeline.add_response_hook(self._script_hook.response_hook)

        self._script_hook.load_script_file(path)
        logger.info("Loaded script file: %s", path)

    def set_script_state(self, key: str, value: any) -> None:
        """Set a value in the script context state.
        
        Useful for initializing state that scripts can access.
        """
        if self._script_hook:
            self._script_hook.set_state(key, value)

    def get_script_state(self, key: str, default: any = None) -> any:
        """Get a value from the script context state."""
        if self._script_hook:
            return self._script_hook.get_state(key, default)
        return default

    # --- Helpers ---

    def _describe_upstream(self) -> str:
        if self.upstream_type == FrameType.TCP:
            return f"{self._upstream.host}:{self._upstream.port}"
        else:
            return f"{self._upstream.serial_port} @ {self._upstream.baudrate} baud"

    def _describe_downstream(self) -> str:
        if self.downstream_type == FrameType.TCP:
            return f"{self._downstream.host}:{self._downstream.port}"
        else:
            return f"{self._downstream.serial_port} @ {self._downstream.baudrate} baud"

    @property
    def is_running(self) -> bool:
        return self._running

    def get_stats(self) -> dict:
        """Get bridge statistics."""
        stats = {
            "running": self._running,
            "upstream_clients": self._upstream.client_count,
            "downstream_connected": self._downstream.is_connected,
            **self._pipeline.get_stats(),
        }
        if self._script_hook:
            stats["script"] = self._script_hook.get_stats()
        return stats
