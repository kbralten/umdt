"""Script hook integration for Mock Server.

This module wraps the core ScriptEngine for use in the mock server context,
enabling user scripts to intercept and modify Modbus requests/responses.

Unlike the bridge, the mock server scripts execute AFTER internal processing
by default (on_response), but can also intercept BEFORE processing (on_request)
to override default behavior or inject custom exceptions.

Typical use cases:
  - Custom response logic based on state
  - Simulate device behavior with counters/timers
  - Log or trace specific operations
  - Inject faults conditionally
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from umdt.core.script_engine import (
    ExceptionResponse,
    ScriptEngine,
    ScriptRequest,
    ScriptResponse,
)

logger = logging.getLogger("umdt.mock_server.script_hook")


class MockServerScriptHook:
    """Script hook integration for the mock server.
    
    Provides request/response interception using the modular ScriptEngine.
    Scripts can:
      - Intercept reads/writes before they reach MockDevice (on_request)
      - Modify responses before sending to client (on_response)
      - Run periodic background logic (on_periodic)
    
    Example script::
    
        # Access counter: increment on every read
        def on_request(req, ctx):
            if req.function_code in (3, 4):  # Read holding/input
                count = ctx.get('access_count', 0) + 1
                ctx.set('access_count', count)
                logger.info(f"Access count: {count}")
            return req
        
        def on_response(resp, ctx):
            # Modify first register value to include access count
            if resp.function_code == 3 and resp.values:
                count = ctx.get('access_count', 0)
                resp.metadata['access_count'] = count
            return resp
    """

    def __init__(
        self,
        scripts: Optional[List[Union[str, Path]]] = None,
        name: str = "mock_server",
    ):
        """Initialize the mock server script hook.
        
        Args:
            scripts: List of script file paths to load
            name: Name for the script engine instance
        """
        self._engine = ScriptEngine(name=name)
        self._periodic_task: Optional[asyncio.Task] = None
        self._running = False

        # Load scripts if provided
        if scripts:
            for script_path in scripts:
                try:
                    self._engine.load_script_file(script_path)
                except Exception as e:
                    logger.error("Failed to load script %s: %s", script_path, e)
                    raise

    @property
    def engine(self) -> ScriptEngine:
        """Get the underlying script engine."""
        return self._engine

    async def process_request(
        self,
        func_code: int,
        address: int,
        count: int,
        unit_id: int,
        values: Optional[List[int]] = None,
    ) -> Union[ScriptRequest, ExceptionResponse, None]:
        """Process an incoming request through script hooks.
        
        Called before the request is processed by MockDevice.
        
        Args:
            func_code: Modbus function code
            address: Starting address
            count: Number of registers/coils
            unit_id: Unit/slave ID
            values: Values for write operations
            
        Returns:
            ScriptRequest: Continue with (possibly modified) request
            ExceptionResponse: Return exception to client
            None: Drop the request silently
        """
        if not self._engine.has_request_hooks():
            # No hooks, pass through
            return ScriptRequest(
                function_code=func_code,
                address=address,
                unit_id=unit_id,
                data=b"",  # Not used in mock server context
                count=count,
                values=values,
            )

        # Build script request
        request = ScriptRequest(
            function_code=func_code,
            address=address,
            unit_id=unit_id,
            data=b"",
            count=count,
            values=values,
        )

        return await self._engine.invoke_request_hook(request)

    async def process_response(
        self,
        func_code: int,
        unit_id: int,
        values: Optional[List[int]] = None,
        is_exception: bool = False,
        exception_code: Optional[int] = None,
        request: Optional[ScriptRequest] = None,
    ) -> Optional[ScriptResponse]:
        """Process an outgoing response through script hooks.
        
        Called after MockDevice has generated a response.
        
        Args:
            func_code: Modbus function code
            unit_id: Unit/slave ID
            values: Response values (for read operations)
            is_exception: Whether this is an exception response
            exception_code: Exception code if is_exception
            request: Original request (if available)
            
        Returns:
            ScriptResponse: Send (possibly modified) response
            None: Drop the response silently
        """
        if not self._engine.has_response_hooks():
            # No hooks, build passthrough response
            return ScriptResponse(
                function_code=func_code,
                unit_id=unit_id,
                data=b"",
                is_exception=is_exception,
                exception_code=exception_code,
                values=values,
                request=request,
            )

        # Build script response
        response = ScriptResponse(
            function_code=func_code,
            unit_id=unit_id,
            data=b"",
            is_exception=is_exception,
            exception_code=exception_code,
            values=values,
            request=request,
        )

        return await self._engine.invoke_response_hook(response)

    async def start_periodic_hooks(self, interval: float = 1.0) -> None:
        """Start the periodic hook runner.
        
        Args:
            interval: Seconds between periodic hook invocations
        """
        if self._running or not self._engine.has_periodic_hooks():
            return

        self._running = True

        async def _run_periodic():
            while self._running:
                try:
                    await self._engine.invoke_periodic_hook()
                except Exception as e:
                    logger.exception("Periodic hook error: %s", e)
                await asyncio.sleep(interval)

        self._periodic_task = asyncio.create_task(_run_periodic())
        logger.info("Periodic hooks started (interval=%.1fs)", interval)

    async def stop(self) -> None:
        """Stop the periodic hook runner."""
        self._running = False
        if self._periodic_task:
            self._periodic_task.cancel()
            try:
                await self._periodic_task
            except asyncio.CancelledError:
                # Expected when cancelling the periodic task; safe to ignore.
                pass
            self._periodic_task = None
            logger.info("Periodic hooks stopped")

    def get_state(self, key: str, default: Any = None) -> Any:
        """Get a value from script state."""
        return self._engine.get_state(key, default)

    def set_state(self, key: str, value: Any) -> None:
        """Set a value in script state."""
        self._engine.set_state(key, value)

    def get_stats(self) -> Dict[str, Any]:
        """Get script engine statistics."""
        return self._engine.get_stats()

    def has_hooks(self) -> bool:
        """Check if any hooks are registered."""
        return (
            self._engine.has_request_hooks()
            or self._engine.has_response_hooks()
            or self._engine.has_periodic_hooks()
        )
