"""Mock Server Script example: Access Counter (async-modern)

This script demonstrates the ScriptEngine API with request hooks. It counts
read/write operations and can block writes to a protected address range.

Hooks:
- `on_request(request, ctx)` - inspect/short-circuit requests

Usage (mock server):
    python mock_server_cli.py start --script examples/scripts/mock_server_counter.py
"""

PROTECTED_START = 1000
PROTECTED_END = 1100


async def on_request(req, ctx):
    """Count operations and block writes to protected addresses.
    
    Returns:
        req - pass the request through to the mock server
        ExceptionResponse - block the request with an exception
    """
    try:
        fc = getattr(req, 'function_code', None)
        addr = getattr(req, 'address', None)

        # Initialize counters on first call
        if 'read_count' not in ctx.state:
            ctx.state['read_count'] = 0
            ctx.state['write_count'] = 0
            ctx.log.info("mock_server_counter initialized")

        if fc in (1, 2, 3, 4):
            ctx.state['read_count'] = ctx.state.get('read_count', 0) + 1
            ctx.log.info("Read #%d: FC%s addr=%s", ctx.state['read_count'], fc, addr)

        elif fc in (5, 6, 15, 16):
            ctx.state['write_count'] = ctx.state.get('write_count', 0) + 1
            ctx.log.info("Write #%d: FC%s addr=%s", ctx.state['write_count'], fc, addr)

            # Block writes to protected address range
            if addr is not None and PROTECTED_START <= addr < PROTECTED_END:
                ctx.log.warning("Blocked write to protected address %s", addr)
                return ctx.make_response_exception(req, exception_code=0x02)

    except Exception:
        ctx.log.exception("on_request error in mock_server_counter")

    # Pass through: return the request unchanged
    return req
