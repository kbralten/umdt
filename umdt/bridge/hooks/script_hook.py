"""Script hook for the Bridge pipeline.

This hook integrates the ScriptEngine with the Bridge pipeline, allowing
user-defined Python scripts to intercept and modify Modbus traffic.

Usage:
    from umdt.bridge.hooks.script_hook import ScriptHook

    hook = ScriptHook()
    hook.load_script('''
        def on_request(req, ctx):
            # Safety interlock example
            if req.function_code == 6 and req.address == 100:
                if not ctx.state.get('SYSTEM_READY'):
                    return ExceptionResponse(0x02)
            return req
    ''')

    bridge.pipeline.add_ingress_hook(hook.ingress_hook)
    bridge.pipeline.add_transform_hook(hook.transform_hook)
    bridge.pipeline.add_response_hook(hook.response_hook)

Example scripts:

    # Block writes to protected registers
    def on_request(req, ctx):
        PROTECTED = [100, 101, 102]
        if req.function_code in (6, 16) and req.address in PROTECTED:
            logger.warning("Blocked write to protected register %d", req.address)
            return ExceptionResponse(0x02)
        return req

    # Track state from responses
    def on_response(resp, ctx):
        if resp.request and resp.request.address == 50:
            ctx.state['SYSTEM_STATUS'] = resp.values[0] if resp.values else 0
        return resp
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional, Union

from umdt.core.script_engine import (
    ExceptionResponse,
    ScriptEngine,
    ScriptRequest,
    ScriptResponse,
)

from ..pipeline import HookContext, Request, Response
from ..protocol import ModbusPDU

logger = logging.getLogger("umdt.bridge.hooks.script")


class ScriptHook:
    """Script hook for the Bridge pipeline.
    
    This class bridges the ScriptEngine with the Bridge's hook system,
    converting between pipeline Request/Response and script-friendly
    ScriptRequest/ScriptResponse objects.
    """

    def __init__(self, name: str = "bridge"):
        """Initialize the script hook.
        
        Args:
            name: Name for the script engine instance
        """
        self._engine = ScriptEngine(name=name)
        self._stats = {
            "requests_processed": 0,
            "responses_processed": 0,
            "exceptions_generated": 0,
            "requests_blocked": 0,
        }

    @property
    def engine(self) -> ScriptEngine:
        """Access the underlying script engine."""
        return self._engine

    def load_script(self, source: str, name: str = "inline") -> None:
        """Load a script from source code.
        
        Args:
            source: Python script source code
            name: Identifier for this script
        """
        self._engine.load_script(source, name)

    def load_script_file(self, path: Union[str, Path]) -> None:
        """Load a script from a file.
        
        Args:
            path: Path to the script file
        """
        self._engine.load_script_file(path)

    def set_state(self, key: str, value: Any) -> None:
        """Set a value in the script context state.
        
        This allows the Bridge to initialize state that scripts can access.
        """
        self._engine.set_state(key, value)

    def get_state(self, key: str, default: Any = None) -> Any:
        """Get a value from the script context state."""
        return self._engine.get_state(key, default)

    # --- Pipeline Hooks ---

    async def ingress_hook(
        self,
        request: Request,
        context: HookContext,
    ) -> Optional[Request]:
        """Ingress hook - called when request arrives from upstream.
        
        This is a good place for:
          - Access control / blocking dangerous commands
          - Logging / auditing
          - State initialization
        """
        if not self._engine.has_request_hooks():
            return request

        self._stats["requests_processed"] += 1

        # Convert to script-friendly format
        script_req = self._pipeline_to_script_request(request)

        # Invoke script hooks
        result = await self._engine.invoke_request_hook(script_req)

        if result is None:
            self._stats["requests_blocked"] += 1
            return None

        if isinstance(result, ExceptionResponse):
            self._stats["exceptions_generated"] += 1
            # Convert to exception PDU and create response
            return self._create_exception_request(request, result.code)

        # Return original request (scripts can modify in transform hook)
        return request

    async def transform_hook(
        self,
        request: Request,
        context: HookContext,
    ) -> Optional[Request]:
        """Transform hook - modify request before forwarding.
        
        This is a good place for:
          - Address remapping
          - Value scaling/conversion
          - Request modification
        """
        # For now, pass through - transform logic can be added
        return request

    async def response_hook(
        self,
        response: Response,
        context: HookContext,
    ) -> Optional[Response]:
        """Response hook - called when downstream responds.
        
        This is a good place for:
          - State tracking from responses
          - Value modification
          - Logging / telemetry
        """
        if not self._engine.has_response_hooks():
            return response

        self._stats["responses_processed"] += 1

        # Get the original script request if available
        script_req = None
        if response.request:
            script_req = self._pipeline_to_script_request(response.request)

        # Convert to script-friendly format
        script_resp = self._pipeline_to_script_response(response, script_req)

        # Invoke script hooks
        result = await self._engine.invoke_response_hook(script_resp)

        if result is None:
            return None

        # Return original response
        return response

    # --- Conversion Helpers ---

    def _pipeline_to_script_request(self, request: Request) -> ScriptRequest:
        """Convert pipeline Request to ScriptRequest."""
        return ScriptRequest.from_pdu(
            unit_id=request.unit_id,
            function_code=request.function_code,
            data=request.data,
            original=request,
        )

    def _pipeline_to_script_response(
        self,
        response: Response,
        script_req: Optional[ScriptRequest] = None,
    ) -> ScriptResponse:
        """Convert pipeline Response to ScriptResponse."""
        return ScriptResponse.from_pdu(
            unit_id=response.unit_id,
            function_code=response.function_code,
            data=response.pdu.data,
            request=script_req,
            original=response,
        )

    def _create_exception_request(self, request: Request, code: int) -> Optional[Request]:
        """Create a modified request that will generate an exception response.
        
        Note: This is a workaround - ideally we'd return the exception directly.
        For now, we return None to block the request and let the caller handle
        generating the exception response.
        """
        # Store exception info for the response hook to use
        self._engine.set_state("_pending_exception", {
            "transaction_id": request.transaction_id,
            "function_code": request.function_code,
            "code": code,
        })
        # Return None to signal blocking (caller should check pending exception)
        return None

    def get_pending_exception(self) -> Optional[Dict[str, Any]]:
        """Get any pending exception that was generated by a script.
        
        Returns dict with 'transaction_id', 'function_code', 'code' if pending.
        """
        exc = self._engine.get_state("_pending_exception")
        if exc:
            self._engine.set_state("_pending_exception", None)
        return exc

    def build_exception_pdu(self, function_code: int, exception_code: int) -> ModbusPDU:
        """Build an exception PDU for the given function and exception codes."""
        return ModbusPDU(
            function_code=function_code | 0x80,
            data=bytes([exception_code]),
        )

    # --- Statistics ---

    def get_stats(self) -> Dict[str, Any]:
        """Get hook statistics."""
        return {
            **self._stats,
            **self._engine.get_stats(),
        }

    def reset_stats(self) -> None:
        """Reset statistics counters."""
        for key in self._stats:
            self._stats[key] = 0
