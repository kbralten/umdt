"""Example Bridge Script - Safety Interlock

This script demonstrates the Logic Injection capability of the UMDT Bridge.
It implements a simple safety interlock that prevents starting a motor
unless the system is in a READY state.

Usage:
    python bridge.py start --upstream-port 5020 --downstream-host 127.0.0.1 \
        --downstream-port 5021 --script examples/bridge_interlock.py

Script API:
    on_request(req, ctx) -> Request | ExceptionResponse | None
        req.function_code - Modbus function code (3, 6, 16, etc.)
        req.address - Starting register address
        req.unit_id - Target unit ID
        req.count - Number of registers (for reads)
        req.values - List of values (for writes)
        ctx.state - Persistent dictionary for state tracking
        ctx.logger - Logger instance for debugging

    on_response(resp, ctx) -> Response | None
        resp.function_code - Response function code
        resp.is_exception - True if this is an exception response
        resp.values - List of values (for read responses)
        resp.request - Original request (if available)
        ctx.state - Persistent dictionary

    Return values:
        - Return the request/response to pass through (possibly modified)
        - Return ExceptionResponse(code) to send an error to the master
        - Return None to silently drop the request/response

Note: ExceptionResponse, logger, and other helpers are injected by the
ScriptEngine at runtime - no imports needed in script files.
"""

# Register addresses for the example
SYSTEM_STATUS_ADDR = 50      # Register containing system status
MOTOR_START_CMD_ADDR = 100   # Register for motor start command
MOTOR_STOP_CMD_ADDR = 101    # Register for motor stop command

# Status values
STATUS_NOT_READY = 0
STATUS_READY = 1
STATUS_RUNNING = 2
STATUS_FAULT = 3


def on_request(req, ctx):
    """Intercept requests and apply safety logic.
    
    This hook is called for every Modbus request before it's forwarded
    to the downstream device.
    """
    # Check for write to motor start command
    if req.function_code == 6 and req.address == MOTOR_START_CMD_ADDR:
        # Get current system status from our tracked state
        status = ctx.state.get('SYSTEM_STATUS', STATUS_NOT_READY)
        
        if status != STATUS_READY:
            # System is not ready - block the start command
            ctx.logger.warning(
                "Blocked motor START: system status is %d (not READY)",
                status
            )
            # Return Illegal Data Address exception (0x02)
            return ExceptionResponse(0x02)
        
        ctx.logger.info("Allowing motor START command - system is READY")
    
    # Check for write to protected stop command (example: require confirmation)
    if req.function_code == 6 and req.address == MOTOR_STOP_CMD_ADDR:
        # Track that a stop was requested
        ctx.state['STOP_REQUESTED'] = True
        ctx.logger.info("Motor STOP command received")
    
    # Pass through all other requests
    return req


def on_response(resp, ctx):
    """Track state from responses.
    
    This hook is called for every response from the downstream device
    before it's sent back to the upstream master.
    """
    # Track system status from read responses
    if resp.request and resp.request.function_code in (3, 4):  # Read holding/input registers
        if resp.request.address == SYSTEM_STATUS_ADDR and resp.values:
            old_status = ctx.state.get('SYSTEM_STATUS', -1)
            new_status = resp.values[0]
            
            if old_status != new_status:
                ctx.state['SYSTEM_STATUS'] = new_status
                ctx.logger.info(
                    "System status changed: %d -> %d",
                    old_status, new_status
                )
    
    # Log exception responses
    if resp.is_exception:
        ctx.logger.warning(
            "Device returned exception: FC=0x%02X code=%d",
            resp.function_code, resp.exception_code or 0
        )
    
    return resp
