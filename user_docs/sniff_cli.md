# Sniffer CLI Guide

The Sniffer CLI (`sniff_cli.py`) is a lightweight command-line tool for capturing and logging Modbus traffic from either TCP/IP or serial (RTU) connections. It's designed for forensic analysis and debugging, producing PCAP files compatible with Wireshark.

## Basic Usage

The primary command for sniffing is `capture`.

```bash
python sniff_cli.py capture [OPTIONS]
```

### Output Formats

The sniffer supports two output formats, which can be used simultaneously:

1.  **SQLite Database** (`--output` / `-o`):
    *   Logs all traffic to a local SQLite file (default: `umdt_traffic.db`).
    *   Useful for long-term logging or custom querying.
    *   *Note*: This is the default behavior.

2.  **PCAP File** (`--pcap` / `-p`):
    *   Logs traffic to a standard `.pcap` file.
    *   Required for analysis in Wireshark.
    *   *Usage*: You must explicitly provide the `--pcap` argument.

## Capture Modbus TCP Traffic

To capture traffic from a Modbus TCP endpoint:

- `--host <IP>`: The IP address of the Modbus TCP server to sniff.
- `--port <PORT>`: The port number of the Modbus TCP server (default is 502).
- `--output <FILE>`: (Optional) Path to save the captured traffic as a single PCAP file.

**Example: Capture TCP traffic and save to a single PCAP file**
```bash
python sniff_cli.py capture --host 192.168.1.10 --port 502 --output capture.pcap
```

## Capture Modbus RTU (Serial) Traffic

To capture traffic from a serial Modbus RTU line:

- `--serial <PORT>`: The serial port (e.g., `COM3`, `/dev/ttyUSB0`).
- `--baud <RATE>`: The baud rate for the serial connection (e.g., `9600`, `115200`).
- `--output <FILE>`: (Optional) Path to save the captured traffic as a single PCAP file.

**Example: Capture serial/RTU traffic on COM3 at 115200 baud**
```bash
python sniff_cli.py capture --serial COM3 --baud 115200 --output rtu_capture.pcap
```

## Split Stream Capture

For easier analysis in Wireshark, you can capture upstream and downstream traffic into separate PCAP files.

- `--split`: Enable splitting of streams.
- `--out-up <FILE>`: Path for the upstream PCAP file (Master ↔ Sniffer).
- `--out-down <FILE>`: Path for the downstream PCAP file (Sniffer ↔ Slave).

**Example: Capture TCP traffic and split into two PCAP files**
```bash
python sniff_cli.py capture --host 127.0.0.1 --port 5503 --split --out-up upstream.pcap --out-down downstream.pcap
```

## Live Decoding

Even without saving to a PCAP, the `sniff_cli.py` will display live decoded Modbus messages in your console during capture.

## Wireshark Compatibility

The PCAP files produced by the Sniffer CLI include a special 4-byte metadata header. To properly decode these files in Wireshark, you should load the provided Lua wrapper scripts: `umdt_modbus_wrapper.lua` and `umdt_mbap.lua`. Refer to the [main README.md](../../README.md) for detailed instructions on loading these scripts into Wireshark.
