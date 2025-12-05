# UMDT Bridge Guide

The Bridge tool (`bridge.py`) acts as a "soft gateway" or "man-in-the-middle" for Modbus traffic. It routes requests from a Master (Upstream) to a Slave (Downstream), potentially converting protocols (TCP ↔ RTU) or modifying data along the way.

## Basic Usage

The bridge runs entirely from the command line.

```bash
python bridge.py start [OPTIONS]
```

### Connection Topologies

The bridge is defined by its **Upstream** (Client/Master facing) and **Downstream** (Server/Slave facing) interfaces.

1.  **TCP Master → Serial Slave (Common)**
    The bridge listens on a TCP port and forwards to a Serial/RTU line.
    ```bash
    python bridge.py start --upstream-port 502 --downstream-serial COM3 --downstream-baud 9600
    ```

2.  **TCP Master → TCP Slave (Port Forwarding)**
    The bridge listens on one port and forwards to another IP/Port. Useful for intercepting traffic or protocol analysis.
    ```bash
    python bridge.py start --upstream-port 5502 --downstream-host 192.168.1.10 --downstream-port 502
    ```

3.  **Serial Master → TCP Slave (Reverse Gateway)**
    The bridge listens on a Serial port (acting as a Slave) and forwards to a TCP device.
    ```bash
    python bridge.py start --upstream-serial COM1 --downstream-host 192.168.1.10
    ```

4.  **Serial Master → Serial Slave (Repeater/isolator)**
    Connects two serial networks.
    ```bash
    python bridge.py start --upstream-serial COM1 --downstream-serial COM2
    ```

## Traffic Capture (PCAP)

The bridge can log all traffic to PCAP files for analysis in Wireshark.

### Single File Capture
Logs both sides of the conversation to one file.
```bash
python bridge.py start ... --pcap traffic.pcap
```

### Dual-Stream Capture (Recommended)
Splits the traffic into two files:
1.  `upstream.pcap`: Traffic between the Real Master and the Bridge.
2.  `downstream.pcap`: Traffic between the Bridge and the Real Slave.

This is critical for debugging because it lets you see exactly what the bridge received vs. what it sent out.

```bash
python bridge.py start ... --pcap-upstream master_side.pcap --pcap-downstream slave_side.pcap
```

### Wireshark Decoding
To view these files correctly:
1.  Open the `.pcap` in Wireshark.
2.  Ensure you have the UMDT Lua plugins loaded (`umdt_modbus_wrapper.lua` and `umdt_mbap.lua`).
    -   *Quick Load*: Start Wireshark with: `wireshark -X lua_script:umdt_modbus_wrapper.lua -X lua_script:umdt_mbap.lua`
3.  The packets will be decoded as Modbus, and exceptions will be highlighted.

## Scripting & Logic Injection

The Bridge supports python scripts to modify traffic on the fly. This is powerful for:
-   **Fixing Protocol Deviations**: Correcting non-standard device behavior.
-   **Security**: Blocking writes to certain registers.
-   **Address Mapping**: Shifting register addresses for legacy support.

### Loading a Script
Use the `--script` argument. You can load multiple scripts.
```bash
python bridge.py start ... --script scripts/firewall.py
```

### How It Works
Scripts implement hook functions that the bridge calls for every packet.

-   `ingress_hook`: Called when a request arrives from the Master.
-   `egress_hook`: Called before sending to the Slave.
-   `response_hook`: Called when the Slave replies.
-   `upstream_response_hook`: Called before replying to the Master.

**Example: Blocking Write Commands**
```python
from umdt.core.script_engine import ExceptionResponse

async def ingress_hook(req, ctx):
    # Block Function Code 6 (Write Single Register)
    if req.function_code == 6:
        ctx.log.warning(f"Blocked write to {req.address}")
        return ExceptionResponse(0x01) # Illegal Function
    return req
```

For more details on the API, see the [Scripting Guide](./scripting.md).
