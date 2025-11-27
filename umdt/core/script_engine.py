"""Scriptable Logic Engine for UMDT.

This module provides a sandboxed Python execution environment for running
user-defined scripts that can intercept, modify, or block Modbus traffic.

The engine is designed to be modular and reusable across UMDT components:
  - Bridge: Intercept/modify requests between Master and Slave
  - Mock Server: Custom response logic for simulated devices

Usage:
    engine = ScriptEngine()
    engine.load_script('''
        def on_request(req, ctx):
            if req.function_code == 6 and req.address == 100:
                if not ctx.state.get('SYSTEM_READY'):
                    return ExceptionResponse(0x02)  # Illegal Data Address
            return req
    ''')

    result = await engine.invoke_request_hook(request, context)

Script API:
    - on_request(req, ctx) -> Request | ExceptionResponse | None
    - on_response(resp, ctx) -> Response | None
    - on_periodic(ctx) -> None

Available in scripts:
    - ExceptionResponse(code) - Return a Modbus exception
    - Request, Response - Data classes for Modbus messages
    - DataType - Enum for register types
    - logger - Logging instance for script debugging
"""
from __future__ import annotations

import ast
import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

logger = logging.getLogger("umdt.core.script_engine")


class ExceptionCode(IntEnum):
    """Modbus exception codes for script responses."""
    ILLEGAL_FUNCTION = 0x01
    ILLEGAL_DATA_ADDRESS = 0x02
    ILLEGAL_DATA_VALUE = 0x03
    SLAVE_DEVICE_FAILURE = 0x04
    ACKNOWLEDGE = 0x05
    SLAVE_DEVICE_BUSY = 0x06
    NEGATIVE_ACKNOWLEDGE = 0x07
    MEMORY_PARITY_ERROR = 0x08
    GATEWAY_PATH_UNAVAILABLE = 0x0A
    GATEWAY_TARGET_NO_RESPONSE = 0x0B


@dataclass
class ExceptionResponse:
    """Indicates that a script wants to return an exception to the Master.
    
    Usage in scripts::
    
        def on_request(req, ctx):
            if not ctx.state.get('ENABLED'):
                return ExceptionResponse(0x02)  # Illegal Data Address
            return req
    """
    code: int
    message: Optional[str] = None

    def __post_init__(self):
        # Accept both int and ExceptionCode enum
        if isinstance(self.code, ExceptionCode):
            self.code = int(self.code)


@dataclass
class ScriptRequest:
    """Request object passed to on_request hooks.
    
    This is a simplified view of the Modbus request for script access.
    Scripts can read properties and return a modified request or exception.
    """
    function_code: int
    address: int
    unit_id: int
    data: bytes
    count: int = 1
    values: Optional[List[int]] = None
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    # Original object for passthrough
    _original: Any = field(default=None, repr=False)

    @classmethod
    def from_pdu(cls, unit_id: int, function_code: int, data: bytes, original: Any = None) -> "ScriptRequest":
        """Create ScriptRequest from raw PDU data."""
        import struct

        address = 0
        count = 1
        values = None

        # Parse based on function code
        if function_code in (0x01, 0x02, 0x03, 0x04):  # Read functions
            if len(data) >= 4:
                address, count = struct.unpack(">HH", data[:4])
        elif function_code in (0x05, 0x06):  # Write single
            if len(data) >= 4:
                address = struct.unpack(">H", data[:2])[0]
                values = [struct.unpack(">H", data[2:4])[0]]
                count = 1
        elif function_code in (0x0F, 0x10):  # Write multiple
            if len(data) >= 5:
                address, count = struct.unpack(">HH", data[:4])
                byte_count = data[4]
                # Parse values
                values = []
                if function_code == 0x10:  # Write registers
                    for i in range(5, 5 + byte_count, 2):
                        if i + 1 < len(data):
                            values.append(struct.unpack(">H", data[i:i+2])[0])
                # For coils, values are bit-packed

        return cls(
            function_code=function_code,
            address=address,
            unit_id=unit_id,
            data=data,
            count=count,
            values=values,
            _original=original,
        )


@dataclass
class ScriptResponse:
    """Response object passed to on_response hooks.
    
    Scripts can inspect and modify response data before it's sent upstream.
    """
    function_code: int
    unit_id: int
    data: bytes
    is_exception: bool = False
    exception_code: Optional[int] = None
    values: Optional[List[int]] = None
    request: Optional[ScriptRequest] = None
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    # Original object for passthrough
    _original: Any = field(default=None, repr=False)

    @classmethod
    def from_pdu(cls, unit_id: int, function_code: int, data: bytes, 
                 request: Optional[ScriptRequest] = None, original: Any = None) -> "ScriptResponse":
        """Create ScriptResponse from raw PDU data."""
        import struct

        is_exception = bool(function_code & 0x80)
        exception_code = data[0] if is_exception and len(data) >= 1 else None
        values = None

        # Parse response data for read functions
        if not is_exception and function_code in (0x03, 0x04):
            if len(data) >= 1:
                byte_count = data[0]
                values = []
                for i in range(1, 1 + byte_count, 2):
                    if i + 1 < len(data):
                        values.append(struct.unpack(">H", data[i:i+2])[0])

        return cls(
            function_code=function_code,
            unit_id=unit_id,
            data=data,
            is_exception=is_exception,
            exception_code=exception_code,
            values=values,
            request=request,
            _original=original,
        )


@dataclass
class ScriptContext:
    """Context object passed to all script hooks.
    
    Provides:
      - state: Persistent dictionary for inter-call state
      - metadata: Bridge/server configuration and runtime info
      - logger / log: Logging instance for script output
      - schedule_task(): Schedule background async tasks
      - sleep(): Cooperative sleep
      - make_response_exception(): Create Modbus exception response
    """
    state: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("umdt.script"))
    _tasks: List[asyncio.Task] = field(default_factory=list, repr=False)

    @property
    def log(self) -> logging.Logger:
        """Alias for logger, for convenience in scripts."""
        return self.logger

    def get(self, key: str, default: Any = None) -> Any:
        """Get a value from state."""
        return self.state.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set a value in state."""
        self.state[key] = value

    def schedule_task(self, coro) -> asyncio.Task:
        """Schedule an async task to run in background (fire-and-forget).
        
        Tasks are tracked and can be cancelled via cancel_all_tasks().
        """
        task = asyncio.create_task(coro)
        self._tasks.append(task)
        return task

    async def sleep(self, seconds: float) -> None:
        """Cooperative sleep for scripts (wraps asyncio.sleep)."""
        await asyncio.sleep(seconds)

    def make_response_exception(self, request, exception_code: int = 0x02) -> ExceptionResponse:
        """Create a Modbus exception response for the given request.
        
        Args:
            request: The original request (used for context/logging)
            exception_code: Modbus exception code (default 0x02 = Illegal Data Address)
        
        Returns:
            ExceptionResponse object that the engine will convert to proper Modbus exception
        """
        return ExceptionResponse(code=exception_code)

    def cancel_all_tasks(self) -> None:
        """Cancel all scheduled background tasks."""
        for task in self._tasks:
            if not task.done():
                task.cancel()
        self._tasks.clear()


# Script callback type hints
RequestHook = Callable[..., Any]
ResponseHook = Callable[..., Any]
PeriodicHook = Callable[..., Any]


class ScriptError(Exception):
    """Error during script execution."""
    pass


class ScriptLoadError(ScriptError):
    """Error loading or compiling a script."""
    pass


class ScriptExecutionError(ScriptError):
    """Error executing a script hook."""
    pass


class ScriptEngine:
    """Sandboxed Python execution engine for user scripts.
    
    The engine provides a restricted execution environment with:
      - Limited built-ins (no file I/O, network, etc.)
      - Access to Modbus-specific types and utilities
      - State persistence across invocations
      - Logging and error handling
    
    Scripts can define the following hooks:
      - on_request(req, ctx): Called for each incoming request
      - on_response(resp, ctx): Called for each outgoing response
      - on_periodic(ctx): Called on timer (if configured)
    """

    # Built-ins allowed in script sandbox
    SAFE_BUILTINS = {
        # Types
        "True": True,
        "False": False,
        "None": None,
        "int": int,
        "float": float,
        "str": str,
        "bool": bool,
        "list": list,
        "dict": dict,
        "tuple": tuple,
        "set": set,
        "bytes": bytes,
        "bytearray": bytearray,
        # Functions
        "len": len,
        "range": range,
        "enumerate": enumerate,
        "zip": zip,
        "map": map,
        "filter": filter,
        "sum": sum,
        "min": min,
        "max": max,
        "abs": abs,
        "round": round,
        "sorted": sorted,
        "reversed": reversed,
        "all": all,
        "any": any,
        "isinstance": isinstance,
        "hasattr": hasattr,
        "getattr": getattr,
        "setattr": setattr,
        "print": print,  # Redirected to logger
        # Exceptions
        "Exception": Exception,
        "ValueError": ValueError,
        "TypeError": TypeError,
        "KeyError": KeyError,
        "IndexError": IndexError,
    }

    def __init__(self, name: str = "default"):
        """Initialize the script engine.
        
        Args:
            name: Name for this engine instance (for logging)
        """
        self.name = name
        self.context = ScriptContext(
            metadata={"engine_name": name, "loaded_at": time.time()},
            logger=logging.getLogger(f"umdt.script.{name}"),
        )

        # Script hooks
        self._request_hooks: List[RequestHook] = []
        self._response_hooks: List[ResponseHook] = []
        self._periodic_hooks: List[PeriodicHook] = []

        # Loaded scripts
        self._scripts: Dict[str, dict] = {}  # name -> {source, globals, ...}

        # Statistics
        self._stats = {
            "scripts_loaded": 0,
            "request_hooks_invoked": 0,
            "response_hooks_invoked": 0,
            "exceptions_returned": 0,
            "errors": 0,
        }

    def _create_sandbox_globals(self) -> Dict[str, Any]:
        """Create the restricted globals for script execution."""
        # Start with safe builtins
        sandbox: Dict[str, Any] = {"__builtins__": dict(self.SAFE_BUILTINS)}

        # Add Modbus types
        sandbox["ExceptionResponse"] = ExceptionResponse
        sandbox["ExceptionCode"] = ExceptionCode
        sandbox["ScriptRequest"] = ScriptRequest
        sandbox["ScriptResponse"] = ScriptResponse

        # Add logger
        sandbox["logger"] = self.context.logger

        # Add struct for binary operations
        import struct
        sandbox["struct"] = struct

        return sandbox

    def _validate_script(self, source: str) -> None:
        """Validate script source for safety.
        
        Raises:
            ScriptLoadError: If script contains unsafe constructs
        """
        try:
            tree = ast.parse(source)
        except SyntaxError as e:
            raise ScriptLoadError(f"Script syntax error: {e}")

        # Check for dangerous imports or calls
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                # Only allow safe modules
                for alias in node.names:
                    if alias.name not in ("struct", "time", "math", "re", "random"):
                        raise ScriptLoadError(f"Import not allowed: {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.module not in ("struct", "time", "math", "re", "random"):
                    raise ScriptLoadError(f"Import from '{node.module}' not allowed")
            elif isinstance(node, ast.Call):
                # Check for dangerous function calls
                if isinstance(node.func, ast.Name):
                    if node.func.id in ("exec", "eval", "compile", "open", 
                                         "__import__", "globals", "locals"):
                        raise ScriptLoadError(f"Call to '{node.func.id}' not allowed")

    def load_script(self, source: str, name: str = "inline") -> None:
        """Load and compile a script from source code.
        
        Args:
            source: Python source code
            name: Name identifier for this script
            
        Raises:
            ScriptLoadError: If script is invalid or unsafe
        """
        logger.info("Loading script: %s", name)

        # Validate for safety
        self._validate_script(source)

        # Create sandbox and execute script to define functions
        sandbox = self._create_sandbox_globals()

        try:
            exec(compile(source, f"<script:{name}>", "exec"), sandbox)
        except Exception as e:
            raise ScriptLoadError(f"Script compilation error: {e}")

        # Extract hooks
        if "on_request" in sandbox and callable(sandbox["on_request"]):
            self._request_hooks.append(sandbox["on_request"])
            logger.debug("Registered on_request hook from %s", name)

        if "on_response" in sandbox and callable(sandbox["on_response"]):
            self._response_hooks.append(sandbox["on_response"])
            logger.debug("Registered on_response hook from %s", name)

        if "on_periodic" in sandbox and callable(sandbox["on_periodic"]):
            self._periodic_hooks.append(sandbox["on_periodic"])
            logger.debug("Registered on_periodic hook from %s", name)

        # Store script metadata
        self._scripts[name] = {
            "source": source,
            "globals": sandbox,
            "loaded_at": time.time(),
        }
        self._stats["scripts_loaded"] += 1

        logger.info("Script loaded successfully: %s", name)

    def load_script_file(self, path: Union[str, Path]) -> None:
        """Load a script from a file.
        
        Args:
            path: Path to the Python script file
            
        Raises:
            ScriptLoadError: If file cannot be read or script is invalid
        """
        path = Path(path)
        if not path.exists():
            raise ScriptLoadError(f"Script file not found: {path}")

        try:
            source = path.read_text(encoding="utf-8")
        except Exception as e:
            raise ScriptLoadError(f"Cannot read script file: {e}")

        self.load_script(source, name=path.stem)

    async def invoke_request_hook(
        self,
        request: ScriptRequest,
    ) -> Union[ScriptRequest, ExceptionResponse, None]:
        """Invoke all on_request hooks.
        
        Args:
            request: The incoming request
            
        Returns:
            - Modified ScriptRequest to continue processing
            - ExceptionResponse to return an error to master
            - None to silently drop the request
        """
        self._stats["request_hooks_invoked"] += 1

        for hook in self._request_hooks:
            try:
                result = hook(request, self.context)

                # Handle async hooks
                if asyncio.iscoroutine(result):
                    result = await result

                if result is None:
                    logger.debug("Request blocked by script hook")
                    return None
                elif isinstance(result, ExceptionResponse):
                    self._stats["exceptions_returned"] += 1
                    logger.debug("Script returning exception: %d", result.code)
                    return result
                elif isinstance(result, ScriptRequest):
                    request = result
                else:
                    logger.warning("Invalid hook return type: %s", type(result))

            except Exception as e:
                self._stats["errors"] += 1
                logger.exception("Error in request hook: %s", e)
                # Continue to next hook on error

        return request

    async def invoke_response_hook(
        self,
        response: ScriptResponse,
    ) -> Optional[ScriptResponse]:
        """Invoke all on_response hooks.
        
        Args:
            response: The outgoing response
            
        Returns:
            - Modified ScriptResponse to send upstream
            - None to silently drop the response
        """
        self._stats["response_hooks_invoked"] += 1

        for hook in self._response_hooks:
            try:
                result = hook(response, self.context)

                # Handle async hooks
                if asyncio.iscoroutine(result):
                    result = await result

                if result is None:
                    logger.debug("Response blocked by script hook")
                    return None
                elif isinstance(result, ScriptResponse):
                    response = result
                else:
                    logger.warning("Invalid hook return type: %s", type(result))

            except Exception as e:
                self._stats["errors"] += 1
                logger.exception("Error in response hook: %s", e)

        return response

    async def invoke_periodic_hook(self) -> None:
        """Invoke all on_periodic hooks."""
        for hook in self._periodic_hooks:
            try:
                result = hook(self.context)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                self._stats["errors"] += 1
                logger.exception("Error in periodic hook: %s", e)

    def has_request_hooks(self) -> bool:
        """Check if any request hooks are registered."""
        return len(self._request_hooks) > 0

    def has_response_hooks(self) -> bool:
        """Check if any response hooks are registered."""
        return len(self._response_hooks) > 0

    def has_periodic_hooks(self) -> bool:
        """Check if any periodic hooks are registered."""
        return len(self._periodic_hooks) > 0

    def get_state(self, key: str, default: Any = None) -> Any:
        """Get a value from the script context state."""
        return self.context.state.get(key, default)

    def set_state(self, key: str, value: Any) -> None:
        """Set a value in the script context state."""
        self.context.state[key] = value

    def clear_state(self) -> None:
        """Clear all script context state."""
        self.context.state.clear()

    def get_stats(self) -> Dict[str, Any]:
        """Get engine statistics."""
        return {
            **self._stats,
            "scripts": list(self._scripts.keys()),
            "request_hooks": len(self._request_hooks),
            "response_hooks": len(self._response_hooks),
            "periodic_hooks": len(self._periodic_hooks),
        }

    def unload_all(self) -> None:
        """Unload all scripts and clear hooks."""
        self._scripts.clear()
        self._request_hooks.clear()
        self._response_hooks.clear()
        self._periodic_hooks.clear()
        logger.info("All scripts unloaded")
