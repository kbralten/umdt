# **Architecture and Implementation Strategy for the Universal Modbus Diagnostic Tool (UMDT)**

## **Executive Summary**

The **Universal Modbus Diagnostic Tool (UMDT)** is a unified Python-based toolkit designed to bridge the gap between ad-hoc scripting and rigid proprietary software in industrial automation. Unlike traditional tools that focus solely on "happy path" communication, UMDT is architected as a comprehensive **Active Client** for inspecting, controlling, and discovering field devices in production environments.

The system leverages a "write once, interface anywhere" philosophy, utilizing a shared asynchronous (asyncio) backend to drive both a robust Command Line Interface (CLI) for headless automation and a rich PySide6 Graphical User Interface (GUI) for interactive analysis. This architecture allows engineers to commission and troubleshoot physical hardware using the same core engine whether at a terminal or in a graphical environment.

> **Note:** UMDT also includes a separate Mock Server component for simulation and testing. See `dev_docs/mock/architecture.md` for details on the Mock Server architecture.

## **1. System Architecture**

UMDT is built on a foundation of shared infrastructure components that enable both CLI and GUI interfaces to access the same diagnostic capabilities without code duplication.

### **1.1 The Shared Foundation**

Both cores rely on a common set of infrastructure components:

* **Asyncio Runtime:** All I/O operations are non-blocking, managed by Python’s asyncio loop.  
* **Transport Abstraction:** A unified layer handles Serial (RTU) and TCP connections, abstracting the physical medium from the application logic.  
* **Typer & Rich:** Used for all CLI interactions to provide type-safe arguments and colored, tabular output.  
* **PySide6 & QAsync:** Used for GUI interactions, bridging the Qt event loop with Python’s asyncio loop to ensure responsive, non-freezing interfaces.

### **1.2 Core 1: The Active Client Engine**

Designed for field work, this engine focuses on:

* **Polymorphic Reading:** Handling single vs. multi-register reads and diverse data types (Coils, Discrete Inputs, Holding Registers, Input Registers).  
* **Complex Writing:** Managing the atomic writing of 32-bit values (Floats/Integers) across multiple 16-bit registers.  
* **Discovery:** Heuristic scanning of address ranges to map unknown devices.  
* **Decoding:** Offline and online translation of raw hex data into human-readable formats (Float16, Float32, UInt, Int) with extensive Endianness support.

### **1.3 Core 2: The Mock Server Engine (Diagnostic Sandbox)**

Designed for lab work and testing, this engine focuses on:

* **Simulation:** Emulating Modbus TCP and RTU endpoints.  
* **State Management:** Maintaining a distinct memory model of registers and coils, organized into logical "Groups".  
* **Fault Injection:** Deliberately introducing latency, packet drops, or bit-flips to stress-test master devices.  
* **Rule Engine:** Enforcing per-address behaviors like "Frozen Values" or "Forced Exceptions."

## **2\. The Active Client Module**

The Client Module is the primary tool for interacting with physical devices. It implements a strict command pipeline to ensure safe and accurate data exchange.

### **2.1 Read and Monitor Logic**

The client supports two modes of data retrieval:

* **Snapshot Read (read):** A single request-response cycle.  
* **Continuous Monitor (monitor):** A looping task that polls the device at a configurable interval.

Endianness & Decoding:  
A key feature of the client is its robust handling of the "Endianness Nightmare."

* **16-bit Support:** Big-Endian (Standard) and Little-Endian.  
* **32-bit Support:** When reading "Long" values (2 registers), the client can decode all four permutations: Big-Endian, Little-Endian, Mid-Big, and Mid-Little.  
* **Visualizer:** The CLI and GUI present these permutations side-by-side, allowing the user to heuristically determine the correct format without trial and error.

### **2.2 The Command Generation Pipeline (write)**

To prevent accidental damage to machinery, write operations follow a strict pipeline:

1. **Input Normalization:** Accepts Decimal, Hex (0x), or Float inputs.  
2. **Type Resolution:** Determines if the target is a 16-bit Integer, 32-bit Integer, or Floating Point value.  
   * *Float Handling:* Automatically converts Python floats to IEEE 754 binary representations (Float16 or Float32).  
3. **Payload Construction:** Splits 32-bit values into two 16-bit words based on the selected Endianness.  
4. **Pre-Flight Check:** Displays a summary table (Register Index, Hex Bytes, Numeric Value) before transmission.

### **2.3 The Scanner Module (scan)**

The scanner is designed to map the memory map of unknown devices.

* **Range Iteration:** Iterates through user-defined start/end addresses.  
* **Type Agnostic:** Can scan Holding Registers, Input Registers, Coils, or Discrete Inputs.  
* **Fault Tolerance:** Silently ignores "Illegal Data Address" exceptions, logging only successful reads to produce a clean map of available points.

### **2.4 The Connection Prober (probe)**

While scan discovers data on a known connection, the Prober discovers the connection itself. This is critical for recovering "lost" devices where the baud rate, slave ID, or IP address is unknown.

 * **Combinatorial Search:** The Prober accepts lists of parameters and iterates through the Cartesian product of all combinations.
 * **TCP Mode:** [192.168.1.10, 192.168.1.11] × [502, 5020] × Unit IDs [1, 255]
 * **Serial Mode:** [COM3, COM4] × [9600, 19200, 115200] × Unit IDs [1-10]
 * **Fast-Fail Transport:** Unlike standard connections which may wait 3s for a timeout, the Prober utilizes a "Hyper-Aggressive" transport configuration (e.g., 100ms timeout) to churn through thousands of combinations quickly.
 * **Success Condition:** The user configures a "Target Register" (e.g., Holding Register 40001). A combination is deemed "Found" if, and only if, a valid Modbus response (Exception or Data) is received for that target.

Output: A list of "Alive" endpoints, allowing the user to immediately transition to scan or monitor mode on the discovered settings.

## **3. Frontend Implementation**

The UMDT Active Client provides two distinct entry points, catering to different workflows.

### **3.1 Command Line Interface (CLI)**

**main\_cli.py**

* **Library:** Typer  
* **Philosophy:** Opinionated, stateless commands.  
* **Wizards:** If connection arguments (--port, \--baud) are omitted, a wizard prompts the user, reducing friction.  
* **Commands:** read, write, scan, probe, monitor, decode.

### **3.2 Graphical User Interface (GUI)**

**main\_gui.py**

* **Library:** PySide6 \+ qasync.  
* **Design:** Tabbed interface (Interact, Monitor, Scan, Probe).  
* **Interact Tab:** Single-shot operations with detailed "Per-Endian" decoding panels.  
* **Monitor Tab:** Scrolling history of polled values with error highlighting.  
* **Scan Tab:** Grid view of discovered registers with real-time progress tracking.  
* **Probe Tab:** Connection discovery with combinatorial parameter search and results table.
* **Connectivity:** Connection panel persists across tabs, managing the CoreController.

## **4. Technical Stack & Dependencies**

| Component | Library | Purpose |
| :---- | :---- | :---- |
| **Language** | Python 3.9+ | Core Runtime |
| **Protocol** | pymodbus | Modbus RTU/TCP Stack |
| **Concurrency** | asyncio | Non-blocking I/O |
| **GUI Framework** | PySide6 (Qt) | Desktop UI |
| **Async-GUI Bridge** | qasync | Integration of Qt and Asyncio loops |
| **CLI Framework** | Typer | Type-safe CLI command definitions |
| **CLI Formatting** | Rich | Colored tables, logs, and progress bars |
| **Serial I/O** | pyserial | Hardware access for RTU |

## **5. Development Workflow**

The project is structured to support rapid development and testing:

* **Entry Points:** Clear separation of main\_cli.py and main\_gui.py ensures focused functionality.  
* **Testing:** pytest is used for unit testing core logic (decoders, frame builders, transport layers).  
* **Integration Testing:** The separate Mock Server (see `dev_docs/mock/architecture.md`) serves as an integration test target.

## **6. Conclusion**

The implemented architecture of the UMDT Active Client successfully delivers a robust diagnostic tool for field work. By providing both CLI and GUI interfaces backed by a common asyncio engine, the tool empowers engineers to discover, read, write, monitor, and probe Modbus devices efficiently in production environments.