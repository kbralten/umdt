"""Example mock server script: Access Counter.

This script demonstrates the logic injection feature for the mock server.
It tracks the number of read and write operations and can modify responses
based on device state.

Usage:
    python mock_server_cli.py start --config config.yaml --tcp-port 5502 \\
        --script examples/mock_server_counter.py

Features demonstrated:
  - on_request: Count and log operations before processing
  - on_response: Add metadata to responses
  - State management via ctx.get/ctx.set

Available in sandbox:
  - logger: Logging instance for script output
  - ExceptionResponse(code): Return a Modbus exception
  - ExceptionCode: Enum of Modbus exception codes
  - ScriptRequest, ScriptResponse: Request/response data classes
  - struct: Binary packing/unpacking
"""

# Note: logger and ExceptionResponse are injected by the sandbox
# Disable linter warnings for these globals

# Track operation counts
def on_request(req, ctx):
    """Count and optionally block operations."""
    read_count = ctx.get('read_count', 0)
    write_count = ctx.get('write_count', 0)

    if req.function_code in (1, 2, 3, 4):  # Read operations
        read_count += 1
        ctx.set('read_count', read_count)
        logger.info(f"Read #{read_count}: FC{req.function_code} addr={req.address} count={req.count}")
    
    elif req.function_code in (5, 6, 15, 16):  # Write operations
        write_count += 1
        ctx.set('write_count', write_count)
        logger.info(f"Write #{write_count}: FC{req.function_code} addr={req.address} values={req.values}")
        
        # Example: Block writes to "protected" address range (1000-1099)
        if 1000 <= req.address < 1100:
            logger.warning(f"Blocked write to protected address {req.address}")
            return ExceptionResponse(0x02)  # Illegal Data Address
    
    return req


def on_response(resp, ctx):
    """Add operation count metadata to responses."""
    # Attach current counts to response metadata for diagnostics
    resp.metadata['read_count'] = ctx.get('read_count', 0)
    resp.metadata['write_count'] = ctx.get('write_count', 0)
    
    # Log successful reads
    if resp.function_code in (3, 4) and resp.values:
        logger.debug(f"Response values: {resp.values[:5]}{'...' if len(resp.values) > 5 else ''}")
    
    return resp
