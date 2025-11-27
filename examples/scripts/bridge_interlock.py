"""Bridge Logic Engine example: Safety Interlock (async-modern)

This script demonstrates an async, non-blocking Logic Engine hook that
implements a simple safety interlock preventing a motor start command
unless the system status register reports READY.

Hooks:
- `ingress_hook(request, ctx)` - called for upstream requests (Master -> Bridge)
- `upstream_response_hook(response, ctx)` - called for responses forwarded to Master

Usage (bridge):
    python bridge.py start --script examples/scripts/bridge_interlock.py
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

async def ingress_hook(request, ctx):
    """Intercept upstream requests and apply safety logic.

    If a write to `MOTOR_START_CMD_ADDR` is attempted while the system
    is not in READY state, return an exception response to block it.
    """
    try:
        # Only care about single-register write (FC=6) in this example
        if getattr(request, 'function_code', None) == 6 and getattr(request, 'address', None) == MOTOR_START_CMD_ADDR:
            status = ctx.state.get('SYSTEM_STATUS', STATUS_NOT_READY)
            if status != STATUS_READY:
                ctx.log.warning("Blocked motor START: system status %s", status)
                # Return an exception response (Illegal Data Address)
                return ctx.make_response_exception(request, exception_code=0x02)
            ctx.log.info("Allowing motor START command - system is READY")

        # Track stop requests as state trigger (non-blocking)
        if getattr(request, 'function_code', None) == 6 and getattr(request, 'address', None) == MOTOR_STOP_CMD_ADDR:
            ctx.state['STOP_REQUESTED'] = True
            ctx.log.info("Motor STOP command received - flagged for processing")

    except Exception:
        ctx.log.exception("ingress_hook error")

    # Pass through by default
    return request

async def upstream_response_hook(response, ctx):
    """Inspect responses forwarded to the master and update internal state.

    This looks for read responses that contain the system status register
    and updates `ctx.state['SYSTEM_STATUS']` so the ingress hook can make
    decisions.
    """
    try:
        req = getattr(response, 'request', None)
        if req and getattr(req, 'function_code', None) in (3, 4):  # read registers
            if getattr(req, 'address', None) == SYSTEM_STATUS_ADDR:
                # Attempt to extract value(s) from response safely
                vals = getattr(response, 'pdu', None)
                if vals is not None:
                    # Best-effort: many engines expose response.pdu.data or response.values
                    new_status = None
                    if hasattr(response, 'values') and response.values:
                        new_status = response.values[0]
                    else:
                        # fallback: try to read bytes
                        pdu = getattr(response, 'pdu', None)
                        if pdu is not None and hasattr(pdu, 'data') and len(pdu.data) >= 2:
                            # naive big-endian 16-bit
                            new_status = int.from_bytes(pdu.data[:2], 'big')

                    if new_status is not None:
                        old = ctx.state.get('SYSTEM_STATUS')
                        if old != new_status:
                            ctx.state['SYSTEM_STATUS'] = new_status
                            ctx.log.info("System status changed: %s -> %s", old, new_status)

    except Exception:
        ctx.log.exception("upstream_response_hook error")

    return response
