# UMDT Hooks & Scripting — Examples and Developer Guide

This document explains how to write, load, test, and debug scripts for the UMDT Mock Server and Bridge. It targets developers who want to extend behavior by intercepting, synthesizing, transforming, or instrumenting Modbus traffic.

Contents
- Introduction
- Runtime model and constraints
- ScriptEngine / Context API (common primitives)
- Mock Server examples
  - Simple counter (timer)
  - Fault injection handler
- Bridge Logic Engine examples
  - Simple translation hook (ingress/egress)
  - Response filtering / masking (response hooks)
- Configuration and runtime loading
- Testing and debugging tips
- Best practices and safety


## Introduction

UMDT provides two scripting surfaces with similar architectures:

- **Mock Server ScriptEngine** — used to simulate devices, synthesize responses, schedule periodic updates, and inject faults. Scripts typically run inside the mock server process and can access register maps.

- **Bridge Logic Engine** — used to inspect and transform traffic that flows through the bridge. Scripts are registered as pipeline hooks and run asynchronously to avoid blocking I/O.

Both engines run Python-based scripts in a controlled async environment and provide a `context` object exposing helpers for I/O, scheduling, state, and logging.


## Runtime model and constraints

- Hooks are asynchronous (use `async def`). Avoid CPU-bound blocking work inside hooks — use `ctx.schedule_task()` or offload heavy work to a background worker.
- Scripts have access to a per-script `state` store for small amounts of state. Do not store large binary objects there.
- Scripts should use provided helpers (`write_register`, `make_response_exception`, etc.) instead of mutating internal server structures directly.
- Script execution must be resilient: always handle exceptions and use `ctx.log` for diagnostics.


## ScriptEngine / Context API (common primitives)

The exact API objects may evolve; below are the commonly available helpers used by examples in the repo. Use them via the `context` or `ctx` parameter passed into hooks.

Common context helpers (examples):
- `ctx.log` — logger instance (use `.info()`, `.debug()`, `.warning()`)
- `ctx.state` — persistent per-script dict-like store
- `ctx.schedule_task(coro)` — schedule an async background task (fire-and-forget managed by engine)
- `await ctx.write_register(unit, address, value)` — convenience writer for mock server register maps
- `await ctx.read_register(unit, address)` — read register value
- `await ctx.sleep(seconds)` — cooperative sleep (alias to asyncio.sleep adapted to engine)
- `ctx.make_response_exception(request, exception_code)` — quick helper returning a Modbus exception response
- `ctx.emit_event(name, payload)` — send internal events to the GUI/CLI for visualization

Note: The above names used in examples mirror utilities in `umdt/mock_server` and `umdt/bridge` helper layers. If you need additional helpers, review the engine API in the codebase and extend safely.


## Mock Server Examples

### 1) Simple counter (periodic timer)
A script that increments a counter every second and writes it into register `1000` for unit `1`.

```python
# examples/scripts/mock_counter.py
from asyncio import sleep

async def on_start(ctx):
    ctx.log.info("mock_counter started")
    ctx.state['count'] = 0

    async def tick():
        while True:
            ctx.state['count'] += 1
            await ctx.write_register(unit=1, address=1000, value=ctx.state['count'])
            await ctx.sleep(1)

    ctx.schedule_task(tick())

async def on_request(request, ctx):
    # Short-circuit reads for a special address
    if request.function_code == 3 and getattr(request, 'address', None) == 9999:
        return ctx.make_response_exception(request, exception_code=1)
    return None
```

Deploy via config or CLI (see `Configuration and runtime loading` below).


### 2) Fault injection handler
Inject an artificial delay and occasional packet drops for a specific Unit ID to test master-side retry logic.

```python
# examples/scripts/fault_injector.py
import random

async def on_request(request, ctx):
    # Only target unit 5
    if request.unit_id != 5:
        return None

    # 10% chance to drop the request (return nothing -> server will continue normal handling)
    if random.random() < 0.10:
        ctx.log.warning("Dropping request for unit 5 to simulate packet loss")
        # Returning a special sentinel may instruct the engine to drop; if not supported,
        # you may schedule to not respond by returning a specific type or raising.
        return ctx.make_response_exception(request, exception_code=0xFF)

    # 50-100ms jitter
    await ctx.sleep(random.uniform(0.05, 0.10))
    return None
```


## Bridge Logic Engine Examples

Bridge hooks receive `Request` or `Response` objects and a `context` with similar helpers as the Mock Server.

### 1) Simple translation hook
Translate requests to a remapped address space before sending downstream.

```python
# examples/scripts/bridge_translate.py
async def ingress_hook(request, ctx):
    # Map master address range 40000-40010 -> slave range 1000-1010
    if 40000 <= request.address <= 40010:
        offset = request.address - 40000
        request.address = 1000 + offset
        ctx.log.debug("Translated request address to %s", request.address)
    return request
```

Register this script in the bridge config or via CLI and it will be invoked for each incoming upstream request.


### 2) Response masking (suppress sensitive values)
Mask a register value before sending it upstream (e.g., hide serial numbers stored at a fixed register).

```python
# examples/scripts/bridge_mask_serial.py
async def upstream_response_hook(response, ctx):
    # Suppose serial number is returned at register address 123
    if response.pdu and getattr(response, 'address', None) == 123:
        # Overwrite PDU bytes with zeros (example - use correct PDU handling)
        response.pdu.data = b'\x00' * len(response.pdu.data)
        ctx.log.info("Masked serial register in response to master")
    return response
```


## Configuration and runtime loading

You can load scripts at startup via YAML config or point the CLI at individual scripts. Examples:

YAML snippet (mock server or bridge config):
```yaml
scripts:
  - path: examples/scripts/mock_counter.py
    enabled: true
  - path: examples/scripts/bridge_translate.py
    enabled: true
```

CLI examples:
```powershell
# Mock server with script
python mock_server_cli.py start --config configs/pump.yaml --script examples/scripts/mock_counter.py

# Bridge with script
python bridge.py start --upstream-port 5503 --downstream-host 127.0.0.1 --downstream-port 5502 --script examples/scripts/bridge_translate.py
```

The CLI supports passing multiple `--script` arguments. The GUI offers an interactive script loader for hot-reload during development.


## Testing and debugging tips

- Use the mock server with a simple script first — ensure `ctx.log` outputs appear in console.
- For bridge hooks, run the bridge locally and point `main_cli.py` or your SCADA simulator at it to exercise hooks.
- Use the dual-stream PCAP logging to capture both upstream and downstream conversations to inspect whether transformations happen where expected:

```powershell
python bridge.py start --pcap-upstream upstream.pcap --pcap-downstream downstream.pcap --upstream-port 5503 --downstream-port 5502
```

- Use the included Wireshark Lua wrappers to decode frames (`umdt_modbus_wrapper.lua` and `umdt_mbap.lua`).
- Add defensive logging in hooks (`ctx.log.debug(...)`) and catch exceptions to avoid crashing the engine:

```python
async def ingress_hook(request, ctx):
    try:
        # your logic
        return request
    except Exception as exc:
        ctx.log.exception("ingress_hook failed: %s", exc)
        return request
```

- Write unit tests for script logic where possible: extract pure transformation functions out of async hooks so they can be tested synchronously.


## Best practices and safety

- Keep hooks small and focused. Complex flows should use background tasks via `ctx.schedule_task()`.
- Avoid blocking operations (disk I/O, long computations) on hook paths.
- Use `ctx.state` only for small pieces of state; persist larger artifacts externally if needed.
- Sanitize and validate incoming data. Do not trust unverified fields.
- Be careful with exception responses and their codes — using exceptions to simulate errors is useful for tests but may confuse connectivity checks if used in production.
- Prefer explicit configuration for enabling scripts (don’t auto-load untrusted scripts in production).


## Where to find examples in this repo

- `examples/scripts/` — sample scripts used by the README examples (create these files as needed).
- `umdt/mock_server/scripts/` — existing example scripts (if present).
- `umdt/bridge/scripts/` — sample bridge hook scripts (if present).


## Next steps for developers

- Copy an example into `examples/scripts/` and run the mock server or bridge with the `--script` flag.
- Use the dual PCAP capture mode of the bridge to confirm transformations and responses.
- If you need helper primitives not exposed by the engine, open a PR to add safe, documented helpers to the ScriptEngine API.


---

If you want, I can also create the example script files themselves in `examples/scripts/` (counter, fault_injector, bridge_translate, bridge_mask_serial) and add minimal unit tests that validate the pure transformation logic. Would you like me to add those files now?