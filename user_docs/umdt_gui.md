# UMDT GUI Guide

The UMDT GUI (`main_gui.py`) provides a user-friendly desktop interface for all the diagnostic capabilities found in the CLI. It is built with PySide6 and is ideal for visual inspection and monitoring.

## Launching the GUI

Run the following command:
```bash
python main_gui.py
```

## Interface Overview

The application is divided into several tabs, each dedicated to a specific function.

### 1. Interact Tab (Read/Write)
This is the main workspace for single-shot operations.

- **Connection Settings**: At the top, select **TCP** or **Serial** and configure parameters (IP/Port or COM/Baud).
- **Read**:
  - Specify the **Unit ID**, **Address**, **Count**, and **Data Type**.
  - Click **Read** to fetch data.
  - Results are shown in a table with columns for:
    - Register Address
    - Raw Hex Value
    - 16-bit Signed/Unsigned Integer
    - Binary
- **Write**:
  - Select a register in the table or manually enter an address.
  - Enter the **Value** to write.
  - Click **Write** to send the command.
- **Decoding**: For 32-bit values (Float/Long), use the "Interpret As" options to see combined values from register pairs.

### 2. Monitor Tab
Use this tab to continuously poll a specific register or range of registers.

- **Setup**: Similar to the Interact tab, set your target address and count.
- **Interval**: Set the polling frequency (e.g., 1000ms).
- **Start/Stop**: Click **Start Monitor** to begin.
- **Visuals**:
  - The table updates in real-time.
  - Changes are often highlighted.
  - Errors (timeouts, exceptions) are logged in the status area.

Behavior notes:
- When you start monitoring, the GUI will clear previously captured monitor samples if the monitored start address, the requested count, or the Long (32-bit) mode setting has changed. This prevents older samples from appearing under new column addresses and keeps captured data aligned with the configured view.
- If you change monitoring parameters but leave the start address, count, and Long mode unchanged, existing captured samples are retained (the display will continue appending new samples). Toggle Long mode on/off will always clear samples because the column interpretation changes (single-register vs double-register columns).
- As a convenience, clicking the Timestamp column or the first data column decodes the first register (or first double-register pair in Long mode). Clicking any other data cell decodes only the register or pair corresponding to that column.

### 3. Scan Tab
Discover readable registers within a specific address range.

- **Range**: Enter **Start Address** and **End Address**.
- **Start**: Click **Scan**.
- **Results**: A list or grid will populate showing which addresses responded successfully. This helps map out an unknown device's memory.

### 4. Probe Tab
Find devices on your network or serial bus.

- **Scope**: Define the range of **Unit IDs** to check (e.g., 1-247).
- **Connection**:
  - For **TCP**, you can specify a range of IP addresses or Ports.
  - For **Serial**, you can try multiple Baud Rates.
- **Start**: Click **Probe**.
- **Output**: The tool attempts to read a known register (usually 0 or 1) from each combination. Successful responses are listed, identifying active devices.

## Tips

- **Connection Status**: The status bar at the bottom often shows the last action's result (Success/Error).
- **Preferences**: Some settings (like last used port) may be saved between sessions.
- **Log**: If something isn't working, check the console window where you launched the GUI for detailed Python logs and tracebacks.
