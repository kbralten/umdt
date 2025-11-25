# **Product Requirements Document (PRD)**

## **Universal Modbus Diagnostic Tool (UMDT)**

| Metadata | Details |
| :---- | :---- |
| **Project Name** | Universal Modbus Diagnostic Tool (UMDT) |
| **Version** | 1.0 |
| **Status** | Draft |
| **Document Type** | Product Requirements Document |
| **Based On** | Integrated Architecture & Implementation Strategy |

## **1\. Introduction & Vision**

### **1.1 Problem Statement**

The industrial automation sector suffers from a fragmentation of diagnostic tools. Engineers currently rely on disparate solutions: rigid, proprietary GUIs for visual debugging (which lack automation) and ad-hoc CLI scripts for automation (which lack visualization). Furthermore, legacy Modbus RTU implementation challenges—specifically timing issues, noise, and non-standard data representations (Endianness)—are not adequately addressed by existing "happy path" tools, leading to prolonged downtime and integration failures.

### **1.2 Product Vision**

To create a unified, professional-grade diagnostic instrument—the **Universal Modbus Diagnostic Tool (UMDT)**. The tool will provide a "write once, interface anywhere" experience, leveraging a shared asynchronous Python backend to drive both a rich Desktop GUI for interactive debugging and a robust CLI for headless operations. It prioritizes safety, deep introspection of physical layers, and high-performance data logging.

### **1.3 Target Audience**

* **Field Engineers:** Require visual feedback, real-time plotting, and "click-to-configure" interfaces for rapid troubleshooting.  
* **Automation Integrators:** Require scriptable CLI tools for load testing, automated validation, and headless gateway deployment.  
* **Embedded Developers:** Require deep protocol analysis, raw byte inspection, and error injection to validate device firmware.

## **2\. Scope**

### **2.1 In-Scope**

* **Protocol Support:** Modbus RTU (Serial) and Modbus TCP (Ethernet).  
* **Operational Modes:** Master (Client), Sniffer (Passive Monitor), and Loopback (Self-Test).  
* **Interfaces:** Command Line Interface (CLI) and Graphical User Interface (GUI).  
* **Data persistence:** High-performance SQLite logging.  
* **Analysis:** Real-time plotting, permissive framing, and endianness decoding.

### **2.2 Out-of-Scope**

* Support for other industrial protocols (Profinet, Ethernet/IP) in V1.  
* Hardware manufacturing (Software only, runs on commodity PC/Raspberry Pi).

## **3\. Functional Requirements**

### **3.1 Core Connectivity & Transport**

| ID | Requirement | Priority | Description |
| :---- | :---- | :---- | :---- |
| **FN-01** | **Transport Abstraction** | P0 | System must treat Serial and TCP connections interchangeably via an abstract transport layer. |
| **FN-02** | **Permissive Framing** | P0 | The tool must capture and log malformed packets (CRC failures) rather than discarding them, to aid in physical layer diagnosis. |
| **FN-03** | **Resource Locking** | P0 | Must implement a TransportLock to coordinate access between continuous "Scanning" tasks and sporadic "Write" tasks to prevent frame interleaving. |
| **FN-04** | **Error Injection** | P2 | Ability to intentionally corrupt CRCs or introduce timing delays to test slave device resilience. |

### **3.2 Passive Sniffer Mode**

| ID | Requirement | Priority | Description |
| :---- | :---- | :---- | :---- |
| **SN-01** | **Electrical Passivity** | P0 | Software must enforce a "Read Only" mode on the serial port to prevent accidental transmission (bus contention) during sniffing. |
| **SN-02** | **Heuristic Reassembly** | P1 | Must implement a sliding window decoder to recover valid frames from fragmented byte streams caused by OS latency. |
| **SN-03** | **Timing Compensation** | P1 | Must utilize kernel-level driver timeouts (inter\_byte\_timeout) to approximate Modbus $t\_{3.5}$ timing requirements. |

### **3.3 Data Handling & Analysis**

| ID | Requirement | Priority | Description |
| :---- | :---- | :---- | :---- |
| **DA-01** | **Simultaneous Decode** | P1 | When viewing a register, the UI must display values decoded in all 4 standard Endian formats (Big, Little, Mid-Big, Mid-Little) simultaneously. |
| **DA-02** | **IEEE 754 Support** | P0 | Full support for 32-bit and 64-bit Floating Point values, including visualization of NaN and Infinity states. |
| **DA-03** | **Heuristic Scanning** | P2 | Implementation of an "Intelligent Scanner" that utilizes Function Code 43 (Device ID) and priority lists to accelerate device discovery. |
| **DA-04** | **PCAP Export** | P2 | Ability to export captured traffic to .pcap format with pseudo-headers for analysis in Wireshark. |

### **3.4 Write Operations (Control)**

| ID | Requirement | Priority | Description |
| :---- | :---- | :---- | :---- |
| **WR-01** | **Command Pipeline** | P0 | All write operations must pass through a pipeline that handles input normalization, payload encoding, and safety checks. |
| **WR-02** | **Write Intent Model** | P1 | Support for "Complex Writes" (e.g., writing a Float32) where the tool handles the underlying register splitting and byte ordering automatically. |
| **WR-03** | **Macro System** | P2 | Ability to record a sequence of write commands (e.g., "Reset Sequence") and save them as a named Macro for replay. |

### **3.5 Data Logging & Persistence**

| ID | Requirement | Priority | Description |
| :---- | :---- | :---- | :---- |
| **LOG-01** | **SQLite Backend** | P0 | All logs must be stored in a SQLite database for transactional integrity. |
| **LOG-02** | **High Throughput** | P0 | Database must use Write-Ahead Logging (WAL) to support concurrent GUI reads and CLI writes without blocking. |
| **LOG-03** | **Session Replay** | P1 | Ability to replay a recorded session, using original timestamps to reproduce the exact cadence of traffic. |

## **4\. User Interface Requirements**

### **4.1 Command Line Interface (CLI)**

* **Tech Stack:** Python Typer \+ Rich.  
* **REQ-CLI-01:** Must provide colored tabular output for traffic logs (Blue=Function, Red=Error).  
* **REQ-CLI-02:** Must support interactive "Wizard" mode for complex commands if arguments are missing.  
* **REQ-CLI-03:** Must support subcommands: umdt scan, umdt monitor, umdt sniff, umdt write.

### **4.2 Graphical User Interface (GUI)**

* **Tech Stack:** PySide6 (Qt).  
* **REQ-GUI-01:** **Register Watcher:** Real-time plotting of register values (up to 60 FPS) using pyqtgraph.  
* **REQ-GUI-02:** **Workspace:** Dockable panels allowing users to arrange "Traffic Log", "Plotter", and "Packet Builder" views.  
* **REQ-GUI-03:** **In-Place Editing:** Excel-style editing of register values directly in the data grid.  
* **REQ-GUI-04:** **Control Panel:** A "Widget Board" allowing users to map toggles and sliders to specific Modbus addresses.

## **5\. Non-Functional Requirements**

### **5.1 Performance**

* **Responsiveness:** The GUI must not freeze during network I/O. (Solution: qasync integration).  
* **Capacity:** The Traffic Log view must handle 100,000+ rows without significant memory overhead (Virtual scrolling/Pagination).  
* **Timing:** Replay engine must compensate for sleep() drift to maintain timing accuracy.

### **5.2 Reliability & Safety**

* **Crash Resilience:** A crash in the GUI should not corrupt the log data (ensured by SQLite WAL).  
* **Bus Safety:** The tool must never interrupt a multi-byte frame transmission when inserting a Write command (ensured by Transport Locking).

### **5.3 Compatibility**

* **OS:** Windows 10/11, Linux (Debian/Ubuntu), macOS.  
* **Hardware:** Standard USB-RS485 dongles, Raspberry Pi UART (GPIO).

## **6\. Technical Constraints**

* **Language:** Python 3.9+.  
* **Concurrency Model:** asyncio (no threading for core logic).  
* **Libraries:** pymodbus (Base protocol), pyserial (Driver), typer (CLI), pyside6 (GUI), sqlite3 (Storage).

## **7\. Success Metrics**

* **Loopback Validity:** 100% of frames sent by Master are detected by Sniffer with correct CRCs during self-test.  
* **Scan Speed:** Intelligent TCP scan of a /24 subnet completes in \< 5 seconds.  
* **Stability:** Capable of logging continuously for 24 hours without memory leaks.