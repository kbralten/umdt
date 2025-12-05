# UMDT CLI Guide

The Command Line Interface (CLI) for UMDT (`main_cli.py`) is a powerful tool for quick diagnostics, scripting, and automation. It allows you to interact with Modbus devices directly from your terminal.

## Basic Usage

The general syntax is:
```bash
python main_cli.py [COMMAND] [OPTIONS]
```

To see available commands:
```bash
python main_cli.py --help
```

To see help for a specific command:
```bash
python main_cli.py [COMMAND] --help
```

## Commands

### 1. Read (`read`)
Reads one or more registers from a device.

**Examples:**
- Read 10 holding registers starting at address 0 from a TCP device:
  ```bash
  python main_cli.py read --host 192.168.1.5 --address 0 --count 10
  ```
- Read a 32-bit float (2 registers) from a serial device (Address 0x100):
  ```bash
  python main_cli.py read --serial COM3 --baud 9600 --unit 1 --address 0x100 --long --count 1
  ```
- **Note**: `--long` reads 2 registers per value (32-bit).

### 2. Write (`write`)
Writes values to registers or coils.

**Examples:**
- Write value `1234` to register 10:
  ```bash
  python main_cli.py write --host 192.168.1.5 --address 10 1234
  ```
- Write a float `12.5` to register 20 (requires 2 registers):
  ```bash
  python main_cli.py write --host 192.168.1.5 --address 20 --float 12.5 --long
  ```
- Turn ON a coil at address 5:
  ```bash
  python main_cli.py write --host 192.168.1.5 --address 5 --datatype coil true
  ```

### 3. Monitor (`monitor`)
Continuously polls registers and displays the values. Useful for watching changing data.

**Example:**
- Poll address 0 every 0.5 seconds:
  ```bash
  python main_cli.py monitor --host 192.168.1.5 --address 0 --interval 0.5
  ```

### 4. Scan (`scan`)
Scans a range of addresses to find which ones are readable. This is useful when you don't know the device's memory map.

**Example:**
- Scan registers 0 to 100:
  ```bash
  python main_cli.py scan 0 100 --host 192.168.1.5
  ```

### 5. Probe (`probe`)
Discovers devices on the network or serial bus by testing different connection parameters (brute-force).

**Example:**
- Find which Unit IDs are active on a serial bus:
  ```bash
  python main_cli.py probe --serial COM3 --units 1-20
  ```
- Find active Modbus TCP devices in a subnet range:
  ```bash
  python main_cli.py probe --hosts 192.168.1.1-255 --ports 502
  ```

### 6. Decode (`decode`)
A utility to decode hex values locally without connecting to a device.

**Example:**
- Decode `0x4120 0x0000` as a float:
  ```bash
  python main_cli.py decode 0x4120 0x0000
  ```

### 7. Ports (`ports`)
Lists available serial ports on your system.

**Example:**
```bash
python main_cli.py ports
```

## Common Options

- **Transport**:
  - TCP: `--host <IP>`, `--port <PORT>` (default 502)
  - Serial: `--serial <PORT>`, `--baud <RATE>` (default 9600)
- **Protocol**:
  - `--unit <ID>`: Modbus Unit ID (Slave ID), default is 1.
  - `--datatype <TYPE>`: `holding` (default), `input`, `coil`, `discrete`.
- **Data Formatting**:
  - `--long`: Treat values as 32-bit (2 registers).
  - `--endian`: Byte order (`big`, `little`, etc.).
  - `--address`: Supports decimal (`100`) or hex (`0x64`).
