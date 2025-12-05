# UMDT Scripting Guide

UMDT includes a powerful Python-based scripting engine that allows you to customize the behavior of the **Mock Server** and the **Bridge**. This guide covers the API, hooks, and common use cases.

## Overview

Scripts in UMDT are standard Python files that implement specific "hook" functions. These hooks are called by the core engine at specific points in the data processing pipeline.

**Key Capabilities:**
- Intercept and modify Modbus requests and responses.
- Maintain internal state (counters, flags, state machines).
- Inject faults (delays, exceptions) conditionally.
- Log custom events.

**Security Note:** Scripts run with full Python permissions. Only load scripts from trusted sources.

## API Reference

The scripting environment provides a context object (`ctx`) and data objects (`request`, `response`) to your hook functions.

### The Context Object (`ctx`)
Passed to every hook, providing access to shared state and utility functions.

- `ctx.state`: A dictionary-like object for persisting data between hook calls.
  ```python
  ctx.state['count'] = 0
  ctx.state['last_active'] = time.time()
  ```
- `ctx.log`: A logger instance.
  ```python
  ctx.log.info("Client connected")
  ```
- `ctx.sleep(seconds)`: Async-friendly sleep.
- `ctx.write_register(unit, address, value)`: (Mock Server only) Helper to update the server's memory map.

### Hook Functions

#### `async def on_request(request, ctx)`
Called when a request is received (Mock Server) or enters the bridge (Bridge Ingress).

- **Arguments**:
  - `request`: Object with attributes `unit_id`, `function_code`, `address`, `count`, `values`, `data`.
  - `ctx`: Context object.
- **Returns**:
  - `request`: The (possibly modified) request to continue processing.
  - `None`: Drop the request silently.
  - `ExceptionResponse(code)`: Return a Modbus Exception immediately (short-circuit).

#### `async def on_response(response, ctx)`
Called before a response is sent back to the client/master.

- **Arguments**:
  - `response`: Object with attributes `unit_id`, `function_code`, `values`, `is_exception`.
  - `ctx`: Context object.
- **Returns**:
  - `response`: The (possibly modified) response to send.
  - `None`: Drop the response.

#### `async def on_periodic(ctx)`
(Optional) Called repeatedly at a fixed interval (default 1s) for background tasks.

## Mock Server Scripting

In the Mock Server, scripts are used to simulate device logic (e.g., a sensor that changes over time, or a pump that turns on/off).

**Loading a Script:**
- **CLI**: `mock_server_cli.py start ... --script my_script.py`
- **Config**:
  ```yaml
  scripts:
    - path: "scripts/simulation.py"
  ```

**Example: Simulating a Ramp Signal**
```python
# scripts/ramp.py

async def on_request(req, ctx):
    # Only care about reads
    return req

async def on_periodic(ctx):
    # Initialize state if missing
    current = ctx.state.get('value', 0)
    
    # Increment
    current = (current + 1) % 100
    ctx.state['value'] = current
    
    # Update register 100 in the mock server memory
    # (Assuming Unit ID 1)
    await ctx.write_register(unit=1, address=100, value=current)
    ctx.log.info(f"Updated register 100 to {current}")
```

**Example: Conditional Faults**
```python
# scripts/fail_after_10.py
from umdt.core.script_engine import ExceptionResponse

async def on_request(req, ctx):
    count = ctx.state.get('count', 0) + 1
    ctx.state['count'] = count
    
    if count > 10:
        ctx.log.warning("Triggering failure mode!")
        return ExceptionResponse(0x04) # Server Device Failure
        
    return req
```

## Bridge Scripting

In the Bridge, scripts act as "middleware" or "filters" for traffic passing between the Upstream (Master) and Downstream (Slave).

**Loading a Script:**
- **CLI**: `bridge.py start ... --script my_filter.py`

**Example: Protecting Critical Registers**
Block write commands (FC 06, 16) to a specific address range.

```python
# scripts/firewall.py
from umdt.core.script_engine import ExceptionResponse

PROTECTED_START = 1000
PROTECTED_END = 1010
WRITE_FUNCS = [6, 16]

async def on_request(req, ctx):
    if req.function_code in WRITE_FUNCS:
        if PROTECTED_START <= req.address <= PROTECTED_END:
            ctx.log.error(f"Blocked write attempt to {req.address}")
            return ExceptionResponse(0x01) # Illegal Function
            
    return req
```

**Example: Protocol Translation (Address Mapping)**
Shift all addresses by +1000 for a legacy device.

```python
# scripts/remap.py

async def on_request(req, ctx):
    # Shift Read Holding (03) addresses down by 1000
    if req.function_code == 3:
        req.address = req.address - 1000
        if req.address < 0:
            return ExceptionResponse(0x02) # Illegal Data Address
            
    return req

async def on_response(resp, ctx):
    # No changes needed for response data typically, 
    # unless logic depends on the original address.
    return resp
```
