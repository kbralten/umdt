
# Universal Modbus Diagnostic Tool (UMDT)

## Overview
UMDT is a Python-based toolkit for diagnosing and exercising Modbus devices (RTU/TCP).
It provides a small, opinionated CLI used for interactive and scripted testing plus helpers
for decoding register values locally.

This README documents the current CLI surface and runtime behavior implemented in
`main_cli.py`.

## Quick Setup
- Install runtime dependencies in your development environment:

```bash
C:/Users/kevin/Dev/umdt/.conda/python.exe -m pip install -r requirements.txt
```

## CLI Commands (current)
- `read` — Read registers from a device (online only).
    - Requires `--serial/--baud` or `--host/--port` (if omitted, a small wizard prompts for connection details).
    - Requires `--address` (decimal or `0xHEX`); `--count` selects number of values; `--long` reads 2 registers per value.
    - `--endian` supports `big|little|mid-big|mid-little` (or `b|l|mb|ml`); `--endian all` shows multiple permutations (only valid for single-value reads).
    - Enforces the Modbus request limit of 125 registers per request.

- `monitor` — Poll a device repeatedly using the same options as `read` (wizard prompts if connection missing).

- `decode` — Local offline decoder for one or two 16-bit registers.
    - Usage examples:
        - Single register (16-bit) decode: `python main_cli.py decode 0x4120`
        - Pair (32-bit) decode: `python main_cli.py decode 0x4120 0x0000`
    - Shows tables for Big/Little (16-bit) or Big/Little/Mid-* (32-bit) permutations and prints Hex/UInt/Int/Float interpretations.

- `write` — Write 16-bit or 32-bit values to a device.
    - Connection/wizard behavior mirrors `read`.
    - `--address` required.
    - By default writes a 16-bit integer; add `--long` to write a 32-bit value (two registers).
    - `--float` accepts float input (disallows `0xHEX`) and writes as Float16 (single register) when used without `--long`, or Float32 (two registers) when used with `--long`.
    - `--signed` validates signed ranges; supplying a negative integer implies signed mode.
    - `--endian` controls byte/word ordering for 16-bit (big/little) and 32-bit (big|little|mid-big|mid-little).
    - Prior to sending the write the CLI prints a small table showing the register index, hex bytes, and numeric value that will be written.

- `ports` — List available serial ports (requires `pyserial`).

## Notes & Implementation Details
- Modbus exception code mapping is provided in `umdt/modbus_exceptions.py` and used by `read`/`monitor` to give human-friendly error messages.
- Float support:
    - Float16 (half) encoding/decoding is supported for single-register operations.
    - Float32 support is available for two-register (`--long`) operations with endian permutations.
- The CLI attempts to be compatible with multiple `pymodbus` versions by adapting to different method signatures (e.g., `unit` vs `slave` keywords) where necessary.

## Examples

Read a single register (wizard will prompt for connection if omitted):

```bash
python main_cli.py read --address 0x10 --serial COM5 --baud 115200
```

Decode locally without a device:

```bash
python main_cli.py decode 0x4120 0x0000
```

Write a 16-bit integer to address 0:

```bash
python main_cli.py write --serial COM5 --baud 115200 --address 0 500
```

Write a 32-bit float (Float32) to address 0 (two registers):

```bash
python main_cli.py write --serial COM5 --baud 115200 --address 0 --long --float -12.5
```

## Development
- Entry points: `main_cli.py` (CLI) and `main_gui.py` (GUI).
- Tests: `pytest` is configured; run locally as shown above.