# Mock Server GUI Guide

The Mock Server GUI (`mock_server_gui.py`) provides a comprehensive dashboard for simulating Modbus devices. It allows you to visualize register states, toggle faults, and manage the server in real-time without memorizing CLI commands.

## Launching the GUI

Run the following command:
```bash
python mock_server_gui.py
```

## Interface Overview

### 1. Configuration & Setup
Before starting the server, you need to configure it:

- **Load Config**: Use the **File > Open Config** menu to load a JSON/YAML register map.
- **Connection Settings**:
  - Choose **TCP** or **Serial**.
  - Set **Port**, **IP**, or **COM/Baud** settings.
- **Start Server**: Click the large **Start** button to begin listening for connections.

### 2. Register Map View
The main area of the window displays the device's memory.

- **Groups**: Registers are organized by the groups defined in your config file (e.g., "Settings", "Live Data").
- **Values**: Real-time values are displayed.
- **Edit**: Double-click a value to change it manually. This simulates a sensor value changing or a setting being updated on the device.

### 3. Fault Injection Panel
A dedicated panel allows you to degrade the server's performance to test how your client/master handles errors.

- **Latency**: A slider to add artificial delay (e.g., 0ms to 5000ms) to every response.
- **Error Rate**: A slider to probabilistically drop packets or send garbage responses.
- **Exception Override**: Force the server to reply with specific Modbus Exceptions (e.g., `Server Busy`, `Illegal Function`) for all requests.

### 4. Traffic Log
The bottom section shows a live log of incoming requests and outgoing responses.
- **Inspect**: Click on a log entry to see detailed packet information (Function Code, Data, Hex dump).
- **Filter**: options to show only Errors or Writes.

### 5. Scripting Control
If you have loaded scripts (via config or the `Scripts` menu):
- **Enable/Disable**: Toggle specific scripts on or off at runtime.
- **Status**: See if a script is running or has encountered an error.

## Typical Workflow

1.  **Design**: Create a `config.yaml` with your desired registers.
2.  **Load**: Open the GUI and load the config.
3.  **Start**: Start the server on localhost:5020.
4.  **Connect**: Point your Modbus Master (or the [UMDT Interactive Tool](./umdt_gui.md)) to this server.
5.  **Test**:
    - Change values in the GUI and verify the Master sees them.
    - Write values from the Master and verify they update in the GUI.
    - Increase the **Latency** slider and verify the Master doesn't time out (or does, if that's the test).
