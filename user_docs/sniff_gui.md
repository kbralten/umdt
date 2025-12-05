# Sniffer GUI Guide

The Sniffer GUI (`sniff_gui.py`) offers a visual front-end for the UMDT Modbus traffic capture capabilities. It allows for live monitoring, basic decoding, and saving of PCAP files without command-line interaction.

## Launching the GUI

Run the following command:
```bash
python sniff_gui.py
```

## Interface Overview

The Sniffer GUI is straightforward, typically featuring:

### 1. Connection Configuration
At the top of the window, you'll find options to configure your capture source:

-   **Transport Selection**: Choose between **TCP** and **Serial** capture.
-   **TCP Settings**:
    -   **Host**: IP address of the Modbus TCP device or interface to listen on.
    -   **Port**: TCP port number (default is 502).
-   **Serial Settings**:
    -   **Serial Port**: Select from a dropdown list of available COM/TTY ports.
    -   **Baud Rate**: Set the baud rate (e.g., 9600, 115200).

### 2. Capture Controls
-   **Start/Stop Button**: Initiates or halts the traffic capture.
-   **Output File**: A field to specify the path for the output `.pcap` file. You might also find options to:
    -   **Split Streams**: A checkbox to enable saving upstream and downstream traffic to separate files (e.g., `_upstream.pcap`, `_downstream.pcap`).

### 3. Live Traffic Display
The main area of the GUI will show a live, scrolling log of the captured Modbus messages.
-   Each entry typically displays:
    -   Timestamp
    -   Source/Destination (e.g., Master/Slave, IP:Port)
    -   Function Code
    -   Unit ID
    -   Address
    -   Value(s) / Data
-   Error messages or malformed packets may be highlighted or logged separately.

### 4. Status Bar / Logs
A status bar or dedicated log panel at the bottom might provide:
-   Current capture status (e.g., "Capturing...", "Stopped").
-   Number of packets captured.
-   Any internal errors or warnings.

## Typical Workflow

1.  **Configure Connection**: Select either TCP or Serial and enter the appropriate connection parameters (Host/Port or Serial Port/Baud Rate).
2.  **Set Output**: Provide a path for the `.pcap` file if you wish to save the capture for later analysis. Optionally, check "Split Streams" for separate upstream/downstream files.
3.  **Start Capture**: Click the "Start" button.
4.  **Monitor Traffic**: Observe the live traffic display.
5.  **Stop Capture**: Click the "Stop" button when you are done. The PCAP file(s) will be finalized.
6.  **Analyze in Wireshark**: Open the generated `.pcap` file(s) in Wireshark. Remember to load the UMDT Lua dissector scripts (`umdt_modbus_wrapper.lua`, `umdt_mbap.lua`) for proper Modbus decoding. Instructions for this are in the [main README.md](../../README.md).
