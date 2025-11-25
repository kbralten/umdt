# **Mock Server Architecture**

## **Executive Summary**

The **Mock Server** component of UMDT acts as a "Digital Twin" for development and testing. It decouples the testing process from physical hardware availability, allowing engineers to validate control logic against a "perfect" or "faulty" simulated device before deploying to the field. The Mock Server provides comprehensive simulation capabilities including fault injection, state management, and runtime control.

## **1. Purpose and Design Philosophy**

The Mock Server is designed for lab work and testing, focusing on:

* **Simulation:** Emulating Modbus TCP and RTU endpoints with full protocol compliance.
* **State Management:** Maintaining a distinct memory model of registers and coils, organized into logical "Groups".
* **Fault Injection:** Deliberately introducing latency, packet drops, or bit-flips to stress-test master devices.
* **Rule Engine:** Enforcing per-address behaviors like "Frozen Values" or "Forced Exceptions."

Unlike static simulators, the UMDT Mock Server provides a dynamic runtime environment that can be modified while running, enabling iterative testing and validation workflows.

## **2. Configuration-Driven Architecture**

The server state is defined by YAML/JSON configuration files. These files describe:

* **Register Groups:** Logical blocks of memory (e.g., "Motor Control", "Temperature Sensors").
* **Initial Values:** Startup states for registers and coils.
* **Transport Settings:** TCP Port or Serial Port parameters.
* **Behavior Rules:** Optional per-address or per-group behaviors (frozen values, forced exceptions).

### Example Configuration Structure

```yaml
groups:
  - name: "Motor Control"
    start_address: 0
    registers: [0, 100, 50, 25]
    type: "holding"
  
  - name: "Temperature Sensors"
    start_address: 100
    registers: [220, 225, 230]
    type: "input"

transport:
  type: "tcp"
  port: 502
  
fault_injection:
  latency_ms: 0
  packet_drop_rate: 0.0
```

## **3. Dynamic Runtime Control**

Unlike static simulators, the UMDT Mock Server exposes a runtime API via CLI REPL and GUI Control Panel.

### **3.1 Interactive REPL (mock_server_cli.py)**

A command loop allowing users to:
* Set register/coil values on-the-fly
* Toggle behavior rules
* Inject faults dynamically
* Monitor incoming requests in real-time
* Save/load configuration snapshots

### **3.2 GUI Control Panel (mock_server_gui.py)**

**Library:** PySide6 + qasync

**Design:** Control Panel Dashboard with the following features:

* **Group Table:** Sortable view of loaded register groups with current values
* **Fault Sliders:** Visual controls to adjust latency and drop rates in real-time
* **Event Log:** Live stream of incoming requests and server responses with timestamps
* **Value Editor:** Manual override widgets for simulation values
* **Rule Management:** Enable/disable behavioral rules per address or group

## **4. Fault Injection System**

To validate the robustness of Master/Client applications, the Mock Server can inject various types of errors:

### **4.1 Network-Level Faults**

* **Latency Injection:** Artificial delays (configurable 0-5000ms) to test timeout logic
* **Packet Drop:** Random dropping of requests/responses to simulate unreliable networks
* **Partial Response:** Send incomplete frames to test frame parsing robustness

### **4.2 Protocol-Level Faults**

* **Forced Exceptions:** Configure specific registers to always return Modbus exceptions
  * 0x01 - Illegal Function
  * 0x02 - Illegal Data Address
  * 0x03 - Illegal Data Value
  * 0x04 - Slave Device Failure
* **CRC Corruption:** Intentionally corrupt CRC/checksum for RTU mode
* **Function Code Errors:** Return unexpected function codes

### **4.3 Data-Level Faults**

* **Bit Flips:** Randomly flip bits in register values to simulate electrical noise
* **Frozen Values:** Lock specific registers to fixed values regardless of write attempts
* **Value Drift:** Gradually change values over time to simulate sensor drift

## **5. State Management**

The Mock Server maintains an internal memory model that mirrors real Modbus devices:

### **5.1 Memory Organization**

* **Coils (0x):** Read/Write single-bit values (0x01, 0x05, 0x0F)
* **Discrete Inputs (1x):** Read-only single-bit values (0x02)
* **Input Registers (3x):** Read-only 16-bit values (0x04)
* **Holding Registers (4x):** Read/Write 16-bit values (0x03, 0x06, 0x10)

### **5.2 Group-Based Organization**

Memory is organized into named groups for easier management:
* Each group represents a logical subsystem (e.g., "Pump Controls", "Temperature Zone 1")
* Groups have metadata: name, description, starting address, length
* Groups can have shared behavior rules (e.g., entire group is read-only)

### **5.3 Persistence**

* **Snapshots:** Save current state to file for later restoration
* **Auto-Save:** Optionally persist changes to configuration file
* **Reset:** Restore to initial configuration state

## **6. Transport Layer**

The Mock Server supports both Modbus TCP and Modbus RTU:

### **6.1 TCP Transport**

* **Port Configuration:** Configurable listening port (default 502)
* **Multi-Client:** Supports multiple simultaneous client connections
* **Connection Tracking:** Logs client connections/disconnections
* **Per-Client State:** Optional isolated memory per client

### **6.2 RTU Transport**

* **Serial Port:** Configurable COM port and baud rate
* **Slave ID:** Configurable unit identifier (1-247)
* **Timing:** Accurate inter-frame spacing per Modbus RTU specification
* **Collision Detection:** Simulates RS-485 bus contention if needed

## **7. Diagnostics and Monitoring**

### **7.1 Event Stream**

All Modbus transactions are logged with detailed information:
* Timestamp (millisecond precision)
* Client identifier (IP/port for TCP, always present for RTU)
* Function code
* Address range
* Data payload (for writes)
* Response type (success, exception, timeout)
* Processing time

### **7.2 Statistics Dashboard**

Real-time metrics:
* Total requests processed
* Requests per second
* Exception rate
* Average response time
* Per-function-code breakdown

## **8. Use Cases**

### **8.1 Development Testing**

Develop and test SCADA/HMI applications without physical devices:
1. Configure Mock Server with expected device memory map
2. Connect application to Mock Server
3. Validate read/write operations
4. Test error handling with fault injection

### **8.2 Regression Testing**

Automated testing of client applications:
1. Define test scenarios in configuration files
2. Start Mock Server programmatically
3. Run client test suite
4. Verify expected interactions via event log
5. Use fault injection to test edge cases

### **8.3 Training and Demonstration**

Safe environment for learning Modbus:
1. No risk of damaging equipment
2. Instant feedback on protocol interactions
3. Ability to pause and inspect state
4. Repeatable scenarios

### **8.4 Protocol Debugging**

Analyze and debug Modbus communication issues:
1. Capture problematic interaction with real device
2. Configure Mock Server to reproduce behavior
3. Use event log to analyze frame-by-frame
4. Test fixes in controlled environment

## **9. Technical Implementation**

### **9.1 Asyncio Architecture**

The Mock Server is built on Python's asyncio for efficient concurrent handling of multiple clients:
* Non-blocking I/O for all network operations
* Event-driven request processing
* Async state updates for real-time GUI synchronization

### **9.2 Pymodbus Integration**

Leverages pymodbus server framework:
* Built on proven Modbus protocol implementation
* Extends with custom data stores and behaviors
* Maintains full protocol compliance

### **9.3 Configuration Management**

* **YAML/JSON:** Human-readable configuration format
* **Schema Validation:** Validate configurations before loading
* **Hot Reload:** Apply configuration changes without restart (where possible)

## **10. Command Line Interface**

**mock_server_cli.py** provides a complete CLI for server management:

### Commands

* `start` - Start server with specified configuration
* `stop` - Gracefully stop running server
* `status` - Show server status and statistics
* `set` - Modify register/coil values at runtime
* `inject` - Control fault injection parameters
* `groups` - List/manage register groups
* `log` - Control logging verbosity and output

### Interactive REPL

Once started, the CLI enters REPL mode with commands:
* `get <address>` - Read register value
* `set <address> <value>` - Write register value
* `freeze <address>` - Freeze register (ignore writes)
* `unfreeze <address>` - Unfreeze register
* `fault latency <ms>` - Set latency injection
* `fault drop <rate>` - Set packet drop rate
* `stats` - Show statistics
* `help` - List available commands
* `quit` - Stop server and exit

## **11. Graphical User Interface**

**mock_server_gui.py** provides a rich visual interface:

### Main Window Layout

1. **Connection Panel** (top)
   - Transport selection (TCP/RTU)
   - Port/Baud configuration
   - Start/Stop controls
   - Status indicator

2. **Groups Table** (left)
   - List of all register groups
   - Current values
   - Sortable columns
   - Click to edit

3. **Event Log** (center)
   - Scrolling list of transactions
   - Color-coded by type
   - Expandable details
   - Export capability

4. **Fault Controls** (right)
   - Latency slider
   - Drop rate slider
   - Bit flip controls
   - Exception injection

5. **Statistics Panel** (bottom)
   - Request counters
   - Performance metrics
   - Charts/graphs

## **12. Extension Points**

The Mock Server is designed for extensibility:

### **12.1 Custom Behaviors**

Implement custom register behaviors:
```python
class CustomBehavior(RegisterBehavior):
    def on_read(self, address):
        # Custom logic
        return value
    
    def on_write(self, address, value):
        # Custom logic
        return success
```

### **12.2 Custom Fault Injection**

Add new fault types:
```python
class CustomFault(FaultInjector):
    def should_inject(self):
        # Injection logic
        return True
    
    def inject(self, frame):
        # Modify frame
        return modified_frame
```

### **12.3 Protocol Extensions**

Support non-standard Modbus extensions:
* Custom function codes
* Vendor-specific behaviors
* Enhanced diagnostics

## **13. Best Practices**

### **13.1 Configuration Management**

* Version control configuration files
* Use descriptive group names
* Document expected behaviors
* Keep configurations minimal and focused

### **13.2 Fault Injection**

* Start with no faults, add incrementally
* Test one fault type at a time
* Document fault scenarios
* Use realistic fault parameters

### **13.3 Testing Workflows**

* Separate configurations per test scenario
* Automate server startup/shutdown
* Capture event logs for analysis
* Use statistics to validate performance

## **14. Conclusion**

The Mock Server component of UMDT provides a comprehensive simulation and testing platform for Modbus applications. By combining flexible configuration, runtime control, sophisticated fault injection, and both CLI and GUI interfaces, it empowers developers to thoroughly test and validate their applications before deployment to physical hardware. The server's extensibility ensures it can adapt to project-specific requirements while maintaining its core mission of providing a reliable, feature-rich Modbus simulation environment.
