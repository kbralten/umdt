
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
pip install -r requirements.txt
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

## GUI (interactive)
A PySide6/qasync-based GUI is included as an interactive alternative to the CLI. It mirrors the main CLI functionality for `read`, `monitor`, and `write` while providing richer per-value decoding and a live view of activity.

- Launch: `python main_gui.py` (requires `PySide6`, `qasync` and the same runtime deps in `requirements.txt`).
- Top connection panel: choose Serial/TCP, set port/host, baud, and Unit ID; `Connect` toggles controller state.
- Tabs:
    - **Interact** — single-shot `Read` and `Write` panels with input validation, an immediate results table, and a details panel that shows per-endian decoding (Hex, UInt/Int, Float16/Float32). `--long` (32-bit) reads show 32-bit permutations; single-register reads show Big/Little 16-bit interpretations.
    - **Monitor** — continuous polling with a scrolling history table and selectable rows. Selected rows populate the same decoding details panel. Monitor supports configurable poll interval and error/highlight rows for failed polls.
- Details panel: when a table row is selected the details widget shows multiple endian permutations and numeric interpretations (mirrors `--endian all` behavior for single-value reads). For 32-bit longs the GUI shows the four common permutations (Big/Little/Mid-Big/Mid-Little).
- Locking and transport: the GUI integrates with `CoreController` where available to reuse shared transport and locking semantics; when a controller isn't started the GUI falls back to thread-wrapped blocking reads/writes (same `pymodbus` compatibility layer used by the CLI).
- Log view & status: lightweight log area shows recent operations (reads/writes/status) and a color-coded status label indicates connection state.

Known limitations / notes:
- `--endian all` is supported for single-value reads (shows Big/Little for 16-bit, and all four permutations for a single 32-bit long). For multi-value reads the CLI/GUI prefer a single selected endian to keep table layouts predictable.
- The GUI currently reuses many CLI helpers; future refactors may extract decoding into a shared module.

## Mock Server (diagnostic sandbox)
UMDT now ships with a configurable Modbus slave that can simulate TCP or serial endpoints, inject faults, and expose register/coil behavior for demos and regression tests.

- CLI entrypoint: `python mock_server_cli.py`
    - `start --config configs/pump.json --tcp-host 0.0.0.0 --tcp-port 15020 --interactive` launches the server and opens a small REPL for runtime edits (set register values, toggle rules, update fault knobs, tail diagnostics events).
    - `groups add/list/remove/reset` modify register groups within a JSON/YAML config.
    - `values set/clear` manage per-address rules (frozen values, ignore-write, forced exceptions).
    - `faults inject` patches default latency/drop/bit-flip settings in configs.
    - `status` prints a summary (unit id, group count, latency, transport) for a config file.
- GUI entrypoint: `python mock_server_gui.py`
    - Provides a live control panel for loading a config, starting/stopping TCP or serial transports, writing on-the-fly values, applying rules, adjusting fault injection knobs, and viewing diagnostics events streamed from the mock device.
    - Register groups load into a sortable table; manual write/rule widgets target any data type (holding/input/coil/discrete). Fault sliders feed directly into the diagnostics manager.

Configs are YAML/JSON files (see `server.md`) that describe register groups, per-address rules, scripted values, and initial fault profiles. Both the CLI and GUI share the same asyncio-first engine, transport coordinator, and diagnostics manager so switching between front ends is seamless.

## Development
- Entry points: `main_cli.py` (diagnostics CLI), `main_gui.py` (diagnostics GUI), `mock_server_cli.py` (mock server CLI), and `mock_server_gui.py` (mock server GUI).
- Tests: `pytest` is configured.
