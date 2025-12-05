# UMDT User Guide

Welcome to the Universal Modbus Diagnostic Tool (UMDT) user documentation. This guide provides an overview of the tool suite and how to use its various components for diagnosing, simulating, and bridging Modbus networks.

## Introduction

UMDT is a comprehensive toolkit designed for engineers and developers working with Modbus (RTU/TCP) devices. It simplifies tasks such as:
- **Diagnosing**: Reading/writing registers, scanning address ranges, and identifying devices.
- **Simulating**: Creating virtual Modbus slaves with configurable behavior and fault injection.
- **Bridging**: Routing traffic between different transports (e.g., TCP to Serial) and modifying packets on the fly.
- **Sniffing**: Capturing and analyzing Modbus traffic for forensic debugging.

## Getting Started

### Prerequisites
- **Python 3.8+**: Ensure Python is installed and added to your system PATH.
- **Pip**: Python package manager.

### Installation

#### Installer (Windows)
For Windows users, pre-built installers are available from the [GitHub Releases page](https://github.com/kbralten/umdt/releases).
Downloading and running the installer will:
- Install the UMDT tools (CLI and GUI applications) to your system.
- Create Start Menu shortcuts for easy access.
- Optionally add the UMDT programs to your user's PATH environment variable, allowing you to run them directly from the command prompt.

#### From Source
1.  **Clone/Download** the repository.
2.  **Install Dependencies**:
    ```bash
    pip install -r requirements.txt
    ```
3.  **Optional**: For development or running tests, install dev dependencies:
    ```bash
    pip install -r requirements-dev.txt
    ```

## Tool Suite Overview

UMDT consists of four main tools, each serving a specific purpose.

### 1. Interactive Tool (UMDT)
**Purpose**: Direct communication with Modbus devices.
**Entry Points**: `main_cli.py` (CLI), `main_gui.py` (GUI).

Use this tool when you need to:
- Read or write specific registers.
- Monitor data changes in real-time.
- Scan a device to find active registers.
- Probe a network to discover devices (brute-force connection parameters).

**Documentation**:
- [**CLI User Guide**](./umdt_cli.md) - For command-line usage and scripting.
- [**GUI User Guide**](./umdt_gui.md) - For the visual desktop application.

### 2. Mock Server
**Purpose**: Simulate Modbus slave devices.
**Entry Points**: `mock_server_cli.py` (CLI), `mock_server_gui.py` (GUI).

Use this tool when you need to:
- Develop client applications without physical hardware.
- Test error handling by injecting faults (latency, timeouts, exceptions).
- Simulate complex device logic using scripts.

**Documentation**:
- [**CLI User Guide**](./mock_server_cli.md) - For headless simulation and automation.
- [**GUI User Guide**](./mock_server_gui.md) - For visual control and fault injection.

### 3. Bridge
**Purpose**: Route and modify Modbus traffic.
**Entry Point**: `bridge.py` (CLI).

Use this tool when you need to:
- Connect a TCP Master to a Serial (RTU) Slave.
- Inspect traffic between a Master and Slave (Man-in-the-Middle).
- Modify requests or responses on the fly using scripts (e.g., fix protocol quirks, map addresses).
- Capture traffic to PCAP files for Wireshark analysis.

**Documentation**:
- [**Bridge User Guide**](./bridge.md) - Configuration, traffic capture, and scripting.

### 4. Sniffer
**Purpose**: Passive traffic capture.
**Entry Points**: `sniff_cli.py` (CLI), `sniff_gui.py` (GUI).

Use this tool when you need to:
- Capture traffic from a serial line or TCP connection without interfering.
- Analyze communication problems.
- Generate PCAP files for analysis in Wireshark.

**Documentation**:
- [**CLI User Guide**](./sniff_cli.md) - For command-line traffic capture.
- [**GUI User Guide**](./sniff_gui.md) - For visual live capture and saving.

## Common Workflows

### Diagnosing a Device
Use `main_cli.py` or `main_gui.py`.
- **Read**: `python main_cli.py read --host <IP> --address 100 --count 10`
- **Scan**: `python main_cli.py scan 0 1000 --host <IP>`

### Simulating a Test Environment
Use `mock_server_cli.py`.
- **Start Server**: `python mock_server_cli.py start --config configs/example.json`
- **Inject Faults**: Use the GUI or CLI to add latency or error responses to test your master's robustness.

### Analyzing Traffic
1.  **Capture**: Use `sniff_cli.py` or `bridge.py` (if bridging) to generate a `.pcap` file.
    ```bash
    python sniff_cli.py capture --serial COM3 --output traffic.pcap
    ```
2.  **Analyze**: Open `traffic.pcap` in Wireshark. Use the provided Lua scripts (`umdt_modbus_wrapper.lua`, `umdt_mbap.lua`) for proper decoding.

## Advanced Topics

- **Scripting**: Both the Mock Server and Bridge support Python scripting to customize behavior (hooks, custom logic). See the [Scripting Guide](./scripting.md).
- **Wireshark Integration**: Detailed guide on setting up the custom Lua dissectors for deeper packet analysis. See the [Wireshark Setup Guide](./wireshark_setup.md).
