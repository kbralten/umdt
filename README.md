
# Universal Modbus Diagnostic Tool (UMDT)

## Overview
UMDT is a comprehensive Python-based toolkit for diagnosing, simulating, and bridging Modbus devices (RTU/TCP).
It has evolved into three distinct tools:
1. **Interactive Tool**: A CLI and GUI for reading/writing registers, scanning addresses, and probing networks.
2. **Mock Server**: A configurable simulation environment for creating virtual Modbus devices with fault injection.
3. **Bridge**: A soft-gateway for routing Modbus traffic between TCP and Serial (RTU) networks.

## 1. Interactive Tool (CLI & GUI)
The interactive tool is designed for direct communication with Modbus devices.

### CLI (`main_cli.py`)
The CLI provides a suite of commands for quick diagnostics and scripting.

**Common Options:**
- Connection: `--serial COMx --baud 9600` or `--host x.x.x.x --port 502`
- Addressing: `--unit 1 --address 0x10`
- Data: `--count 5`, `--long` (32-bit), `--float`, `--endian big|little`

**Commands:**
- `read`: Read registers (holding/input/coil/discrete).
  ```bash
  python main_cli.py read --host 192.168.1.10 --address 0 --count 10
  ```
- `write`: Write values to registers.
  ```bash
  python main_cli.py write --serial COM3 --address 0 1234
  python main_cli.py write --host 127.0.0.1 --address 10 --float 12.5
  ```
- `monitor`: Poll a device repeatedly (like `read` but continuous).
- `scan`: Discover readable registers in an address range.
  ```bash
  python main_cli.py scan 0 100 --host 192.168.1.10
  ```
- `probe`: Find devices by testing combinations of connection parameters (baud rates, Unit IDs, etc.).
  ```bash
  python main_cli.py probe --serials COM3 --bauds 9600,115200 --units 1-10
  ```
- `decode`: Offline decoder for hex values (no device required).
  ```bash
  python main_cli.py decode 0x4120 0x0000
  ```
- `ports`: List available serial ports.

### GUI (`main_gui.py`)
A PySide6-based desktop application that mirrors the CLI functionality with a visual interface.
- **Interact**: Single-shot read/write with detailed decoding (Hex, Int, Float).
- **Monitor**: Continuous polling with history and error highlighting.
- **Scan**: Visual address scanner with real-time results.
- **Probe**: Network discovery tool with exportable results.

Launch with: `python main_gui.py`

## 2. Mock Server (Simulation)
A configurable Modbus slave for development, testing, and demos. It supports fault injection (latency, errors) and complex register mapping.

### CLI (`mock_server_cli.py`)
- `start`: Launch the server.
  ```bash
  python mock_server_cli.py start --config configs/pump.json --tcp-port 5502 --interactive
  ```
- `groups`: Manage register groups.
- `values`: Set static values or rules (e.g., freeze value, error on write).
- `faults`: Inject network faults (latency, packet drops).

### GUI (`mock_server_gui.py`)
A control panel for the mock server to visualize state, modify values on-the-fly, and control fault injection sliders.

### Configuration
Configs are YAML/JSON files defining register maps and initial state. See `server.md` for details.

## 3. Bridge (Soft-Gateway)
A transparent bridge for routing Modbus traffic between different transports (e.g., TCP Master to RTU Slave). It supports protocol conversion and multiple concurrent upstream clients.

### CLI (`bridge.py`)
- `start`: Start the bridge.
  ```bash
  # TCP Master -> RTU Slave (SCADA -> RS-485)
  python bridge.py start --upstream-port 502 --downstream-serial COM3 --downstream-baud 9600

  # TCP Master -> TCP Slave (Port Forwarding/Inspection)
  python bridge.py start --upstream-port 5503 --downstream-host 127.0.0.1 --downstream-port 5502
  ```
- `info`: Show bridge status and capabilities.

## Development Notes

### Setup
1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. (Optional) Install dev dependencies for testing/building:
   ```bash
   pip install -r requirements-dev.txt
   ```

### Project Layout
- `main_cli.py` / `main_gui.py`: Interactive tools.
- `mock_server_cli.py` / `mock_server_gui.py`: Mock server tools.
- `bridge.py`: Bridge entry point.
- `umdt/`: Core package source.
- `tests/`: Pytest suite.

### Testing

Run unit tests:
```bash
pytest
```

End-to-end (E2E) Docker tests
- Requirements: Docker (and `docker compose`) installed. On Windows, WSL2 is recommended for CI-like environments.
- Start the test environment and run the E2E suite:
```bash
docker compose -f tests/e2e/docker-compose.yml up --build --abort-on-container-exit
# in another shell (or after containers are up) run the E2E pytest suite
pytest tests/e2e -q
# OR run only E2E-marked tests
pytest -m e2e
```

UI tests
- UI tests exercise the PySide6 GUI and require the GUI runtime and `pytest-qt` (or equivalent fixtures).
- Install dev deps and run the UI tests:
```bash
pip install -r requirements-dev.txt
pytest tests/ui -q
```
- Notes: Running GUI tests on headless CI typically requires an X server or virtual framebuffer (e.g., `xvfb`) or using a Windows-native test runner. For quick local iteration on Windows, run tests directly in the desktop session.