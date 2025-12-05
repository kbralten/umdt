# Mock Server CLI Guide

The Mock Server CLI (`mock_server_cli.py`) is a versatile tool for simulating Modbus devices. It allows you to create virtual slaves with complex memory maps, scriptable behavior, and network fault injection.

## Basic Usage

The general syntax is:
```bash
python mock_server_cli.py [COMMAND] [OPTIONS]
```

## Core Commands

### 1. Start Server (`start`)
Launches the mock server. You must provide a configuration file (JSON/YAML) that defines the device's register map.

**Examples:**
- **TCP Server**: Start on port 5020 using `pump_station.json` config.
  ```bash
  python mock_server_cli.py start --config configs/pump_station.json --tcp-port 5020
  ```
- **Serial Server**: Start on COM3 at 9600 baud.
  ```bash
  python mock_server_cli.py start --config configs/sensor.yaml --serial-port COM3 --serial-baud 9600
  ```
- **With Scripting**: Load a python script to add dynamic logic (e.g., counters, state machines).
  ```bash
  python mock_server_cli.py start --config configs/device.json --script scripts/simulate_temp.py
  ```
- **Interactive Mode**: Starts the server with a command prompt to modify state while running.
  ```bash
  python mock_server_cli.py start --config configs/device.json --interactive
  ```

### 2. Manage Groups (`groups`)
Helper commands to modify the register groups in your configuration file.

- **List Groups**:
  ```bash
  python mock_server_cli.py groups list --config configs/device.json
  ```
- **Add Group**: Create a new block of holding registers.
  ```bash
  python mock_server_cli.py groups add --config configs/device.json --name "Sensors" --type holding --start 100 --count 10
  ```

### 3. Manage Values (`values`)
Set static values or specific behaviors for individual registers in the config.

- **Set Value**: Force register 100 to always return 1234.
  ```bash
  python mock_server_cli.py values set --config configs/device.json --address 100 --value 1234
  ```
- **Set Error**: Force register 101 to return an exception (e.g., Illegal Data Value) on write.
  ```bash
  python mock_server_cli.py values set --config configs/device.json --address 101 --error-on-write 3
  ```

### 4. Fault Injection (`faults`)
Configure network-level faults to test master application resilience.

- **Inject Latency**: Add a 500ms delay to all responses.
  ```bash
  python mock_server_cli.py faults inject --config configs/device.json --latency 0.5
  ```
- **Packet Loss**: Drop 10% of requests.
  ```bash
  python mock_server_cli.py faults inject --config configs/device.json --drop-rate 0.1
  ```

## Configuration File

The mock server relies on a configuration file. You can generate a basic one using the `groups add` command or create one manually:

```yaml
# configs/simple.yaml
device_name: "My Virtual Device"
groups:
  - name: "Status"
    type: "holding"
    start_address: 0
    count: 10
defaults:
    latency: 0.1
```

## Configuration File Reference

The Mock Server uses a YAML or JSON configuration file to define its behavior.

```yaml
# configs/example.yaml
device_name: "Pump Station A"
unit_id: 1             # Default Modbus Slave ID
latency_ms: 10         # Base latency for all requests

groups:
  - name: "Status Registers"
    type: "holding"    # holding, input, coil, discrete
    start: 0
    length: 10
    writable: true     # Can the master write to these?
    description: "System status flags"

  - name: "Sensor Data"
    type: "input"
    start: 100
    length: 5
    writable: false

faults:
  latency_ms: 50       # Global latency override
  drop_rate_pct: 0.1   # Drop 0.1% of packets

rules:
  "10":                # Address 10
    mode: "frozen-value"
    forced_value: 1234
  "11":
    mode: "exception"
    exception_code: 2  # Illegal Data Address
```

## Interactive Mode Reference

When running with `mock_server_cli.py start ... --interactive`, you get a console prompt to control the server while it runs.

**Available Commands:**

*   `help`: Show available commands.
*   `groups`: List all configured register groups.
*   `snapshot`: Dump the current values of all registers to the screen.
*   `set <type> <addr> <value>`: Write a value to a register.
    *   *Example*: `set holding 10 99`
*   `rule <addr> <mode> [value]`: Apply a behavior rule to an address.
    *   *Modes*: `frozen-value`, `exception`, `ignore-write`.
    *   *Example*: `rule 10 exception 2` (Return Exception Code 2 for Address 10)
    *   *Example*: `rule 20 frozen-value 500` (Always return 500, ignore writes)
*   `fault <field> <value>`: Update fault injection parameters.
    *   *Example*: `fault latency_ms 200`
*   `events`: Wait for and display the next internal event (diagnostic log).
*   `quit`: Stop the server and exit.

