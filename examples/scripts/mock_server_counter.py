"""Mock Server Script example: Access Counter (async-modern)

This script demonstrates the async ScriptEngine API: timers, state, and
request hooks. It increments a counter on a timer and writes it into the
mock server register map. It also counts read/write operations and can
block writes to a protected range.

Hooks:
- `on_start(ctx)` - lifecycle startup hook (schedule background work)
- `on_request(request, ctx)` - inspect/short-circuit requests

Usage (mock server):
    python mock_server_cli.py start --script examples/scripts/mock_server_counter.py
"""

from asyncio import sleep

PROTECTED_START = 1000
PROTECTED_END = 1100

async def on_start(ctx):
    ctx.log.info("mock_server_counter started")
    ctx.state['read_count'] = 0
    ctx.state['write_count'] = 0
    ctx.state['count'] = 0

    async def tick():
        while True:
            ctx.state['count'] += 1
            await ctx.write_register(unit=1, address=1000, value=ctx.state['count'])
            await ctx.sleep(1)

    ctx.schedule_task(tick())

async def on_request(req, ctx):
    try:
        fc = getattr(req, 'function_code', None)
        addr = getattr(req, 'address', None)

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

    return None
