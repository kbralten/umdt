# **Architecture and Implementation Strategy for the Universal Modbus Diagnostic Tool (UMDT)**

## **Executive Summary**

The **Universal Modbus Diagnostic Tool (UMDT)** is a unified Python-based toolkit designed to bridge the gap between ad-hoc scripting and rigid proprietary software in industrial automation. Unlike traditional tools that focus solely on "happy path" communication, UMDT is architected as a comprehensive **Diagnostic Suite** comprising two distinct but complementary engines: an **Active Client** for inspecting and controlling field devices, and a **Mock Server** for simulation, regression testing, and fault injection.

The system leverages a "write once, interface anywhere" philosophy, utilizing a shared asynchronous (asyncio) backend to drive both a robust Command Line Interface (CLI) for headless automation and a rich PySide6 Graphical User Interface (GUI) for interactive analysis. This dual-engine approach allows engineers to validate control logic against a "perfect" or "faulty" mock device before deploying to the field, and subsequently use the same toolchain to commission and troubleshoot the physical hardware.

## **1\. System Architecture: The Dual-Core Engine**

The UMDT architecture is divided into two primary operational cores that share a common foundation of libraries and utilities. This separation ensures that the tool can act as a lightweight client or a heavy-duty simulator without unnecessary overhead.

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

## **3\. The Mock Server Module**

The Mock Server acts as a "Digital Twin" for development. It decouples the testing process from physical hardware availability.

### **3.1 Configuration-Driven Architecture**

The server state is defined by YAML/JSON configuration files. These files describe:

* **Register Groups:** Logical blocks of memory (e.g., "Motor Control", "Temperature Sensors").  
* **Initial Values:** Startup states for registers.  
* **Transport Settings:** TCP Port or Serial Port parameters.

### **3.2 Dynamic Runtime Control**

Unlike static simulators, the UMDT Mock Server exposes a runtime API via CLI REPL and GUI Control Panel.

* **Interactive REPL:** A command loop allowing users to set values, toggle rules, or inject faults while the server is running.  
* **Diagnostics Stream:** A live feed of events (Read, Write, Error) broadcast to the frontend for analysis.

### **3.3 Fault Injection System**

To validate the robustness of Master/Client applications, the Mock Server can inject errors:

* **Latency:** Artificial delays to test timeout logic.  
* **Packet Drop:** Simulating unreliable networks.  
* **Bit Flips:** Simulating electrical noise.  
* **Exceptions:** Forcing Modbus Exceptions (e.g., 0x02 Illegal Data Address) on specific registers.

## **4\. Frontend Implementation**

The UMDT provides four distinct entry points, catering to different workflows.

### **4.1 Command Line Interfaces (CLI)**

**main\_cli.py (Client CLI)**

* **Library:** Typer  
* **Philosophy:** Opinionated, stateless commands.  
* **Wizards:** If connection arguments (--port, \--baud) are omitted, a wizard prompts the user, reducing friction.  
* **Commands:** read, write, scan, monitor, decode.

**mock\_server\_cli.py (Server CLI)**

* **Library:** Typer \+ Custom REPL loop.  
* **Philosophy:** Stateful session management.  
* **Capabilities:** Load configs, manage groups, and modify server state in real-time.

### **4.2 Graphical User Interfaces (GUI)**

**main\_gui.py (Client GUI)**

* **Library:** PySide6 \+ qasync.  
* **Design:** Tabbed interface (Interact, Monitor, Scan).  
* **Interact Tab:** Single-shot operations with detailed "Per-Endian" decoding panels.  
* **Monitor Tab:** Scrolling history of polled values with error highlighting.  
* **Scan Tab:** Grid view of discovered registers with real-time progress tracking.  
* **Connectivity:** Connection panel persists across tabs, managing the CoreController.

**mock\_server\_gui.py (Server GUI)**

* **Library:** PySide6 \+ qasync.  
* **Design:** Control Panel Dashboard.  
* **Features:**  
  * **Group Table:** Sortable view of loaded register groups.  
  * **Fault Sliders:** Visual controls to adjust latency and drop rates.  
  * **Event Log:** Live stream of incoming requests and server responses.  
  * **Value Editor:** Manual override widgets for simulation values.

## **5\. Technical Stack & Dependencies**

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

## **6\. Development Workflow**

The project is structured to allow independent evolution of the Client and Server modules while sharing core utilities.

* **Entry Points:** Explicit separation of main\_cli.py, main\_gui.py, mock\_server\_cli.py, and mock\_server\_gui.py ensures clear boundaries of concern.  
* **Configuration:** The use of standard JSON/YAML for server profiles ensures portability and version control of test scenarios.  
* **Testing:** pytest is used for unit testing core logic (decoders, frame builders), while the Mock Server itself serves as the integration test target for the Client.

## **7\. Conclusion**

The implemented architecture of the UMDT successfully realizes the vision of a "Full Stack" diagnostic tool. By moving beyond simple sniffing and implementing a robust Active Client alongside a capable Mock Server, the tool empowers developers and engineers to own the entire communication loop—from generating the signal to simulating the response—within a single, unified Python ecosystem.