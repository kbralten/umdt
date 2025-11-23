# **Development Roadmap**

## **Universal Modbus Diagnostic Tool (UMDT)**

| Metadata | Details |
| :---- | :---- |
| **Project Name** | Universal Modbus Diagnostic Tool (UMDT) |
| **Version** | 1.1 |
| **Status** | Planning |
| **Based On** | Product Requirements Document (PRD) v1.0 |

## **Phase 0: The Skeleton & Proof of Concept (Priority P00)**

*Focus: Validating the "Write Once, Run Anywhere" architecture and async integration before complex protocol logic is added.*

### **POC-01: Project Scaffold & Entry Points**

**Goal:** Establish the directory structure and build system to support dual interfaces.

1. **Dependency Management:** Initialize the project with poetry or pip, defining dependencies for typer, pyside6, qasync, and pymodbus.  
2. **Package Structure:** Create the umdt/ package with distinct submodules: core/, transports/, cli/, gui/, and database/.  
3. **Dual Entry Points:** Create main\_cli.py (runs the Typer app) and main\_gui.py (initializes QApplication). Ensure both can import from umdt.core.

### **POC-02: Async-GUI Bridge ("The Hello World")**

**Goal:** Prove that the GUI does not freeze during async operations (Critical Architectural Risk).

1. **QAsync Integration:** In main\_gui.py, implement the qasync.QEventLoop setup.  
2. **Non-Blocking Test:** Create a simple GUI window with a "Test Async" button.  
3. **Sleep Test:** Wire the button to an async def function that performs await asyncio.sleep(3). Verify that the window remains draggable and responsive during the sleep.

### **POC-03: The Mock Loopback Transport**

**Goal:** Simulate data flow without requiring physical hardware or Modbus libraries yet.

1. **Mock Class:** Create MockTransport that implements the basic send/receive methods but simply echoes back a reversed byte string after a random delay.  
2. **Controller Integration:** Instantiate CoreController using this mock transport.  
3. **End-to-End Flow:**  
   * Trigger a "Send" from the CLI.  
   * Have the MockTransport echo the data.  
   * Have the CoreController log this echo to a shared list.  
   * Verify the GUI (if running) updates to show this log entry via an observer signal.

## **Phase 1: The Iron Core (Priority P0)**

*Focus: Establishing the fundamental architecture, safety mechanisms, and data persistence layer required for a minimum viable product.*

### **FN-01: Transport Abstraction**

**Goal:** Decouple business logic from physical interfaces (Serial vs. TCP).

1. **Define Interface Contract:** Create the abstract base class TransportInterface defining mandatory methods (connect, disconnect, send\_pdu, receive\_frame) and event hooks.  
2. **Implement TCP Transport:** Develop TcpTransport using Python's asyncio.open\_connection, handling socket buffering and stream management.  
3. **Implement Serial Transport:** Develop SerialTransport using pyserial-asyncio, mapping asyncio streams to the UART buffer.  
4. **Connection Manager Factory:** Create the ConnectionManager singleton that instantiates the correct transport based on a unified configuration object (URI parsing).  
5. **Auto-Reconnect Logic:** Implement exponential backoff algorithms within the ConnectionManager to handle temporary link failures transparently.

### **FN-02: Permissive Framing**

**Goal:** Allow the tool to "see" what standard libraries ignore (garbage data).

1. **Subclass Pymodbus Framers:** Create UMDT\_RtuFramer and UMDT\_SocketFramer inheriting from standard pymodbus classes.  
2. **Raw Byte Interception:** Override the processIncomingPacket method to copy the raw input buffer to the logging queue *before* any decoding logic attempts to parse it.  
3. **Soft CRC Validation:** Implement a custom CRC check that flags the frame object with error\_crc=True but does not discard the packet, allowing it to bubble up to the application layer.  
4. **Malform Handling:** Add exception handlers for truncated frames to log them as "Incomplete Fragments" rather than crashing the listener task.

### **FN-03: Resource Locking**

**Goal:** Prevent command collisions when mixing automated scanning and user control.

1. **Global Lock Implementation:** Instantiate a TransportLock (asyncio.Lock) within the CoreController.  
2. **Scanner Compliance:** Wrap the automated background scanning loop in a async with lock: context manager, ensuring it yields the bus between batches.  
3. **Priority Write Access:** Implement a request\_write\_access() method that pauses the scanner task explicitly before acquiring the lock for user-initiated commands.

### **SN-01: Electrical Passivity**

**Goal:** Ensure the "Sniffer" mode cannot physically disrupt the RS-485 bus.

1. **ReadOnly Wrapper:** Create a PassiveTransport decorator or mixin that wraps an existing transport.  
2. **Write Blockade:** Override write, send, and flush methods to raise a RuntimeError ("Operation Forbidden in Sniffer Mode") if called.  

### **DA-02: IEEE 754 Support**

**Goal:** Correctly interpret floating-point sensor data.

1. **Struct Integration:** Implement struct.unpack wrappers for \>f (Float32 Big-Endian) and \>d (Float64 Big-Endian).  
2. **NaN/Inf Handling:** specific checks for math.isnan() and math.isinf() to translate these Python states into user-friendly string representations ("SENSOR FAULT", "OVERFLOW").  
3. **Integration with Register Map:** Update the data model to map two contiguous 16-bit registers to a single 32-bit float value view.

### **WR-01: Command Pipeline**

**Goal:** Safety and validation for outgoing commands.

1. **Input Validation Layer:** Create validtors that check range limits (e.g., 0-65535 for UInt16) before a command is constructed.  
2. **Payload Builder:** Implement the CommandBuilder class using pymodbus.payload.BinaryPayloadBuilder to handle serialization.  
3. **Safe Mode Gate:** Implement a middleware step that checks a global SAFE\_MODE flag; if True, pause execution and emit a signal requiring User Interface confirmation.  
4. **Bus Access Request:** Integrate with **FN-03** to acquire the Transport Lock as the final step of the pipeline.

### **LOG-01 & LOG-02: High-Performance SQLite Logging**

**Goal:** Non-blocking, crash-safe data persistence.

1. **Schema Design:** Define the SQL DDL for the traffic\_log table (timestamp, direction, raw\_bytes, parsed\_json).  
2. **WAL Configuration:** Implement the database initialization routine that executes PRAGMA journal\_mode=WAL and PRAGMA synchronous=NORMAL.  
3. **Async Writer Task:** Create a dedicated LogWorker task that pulls packet objects from a asyncio.Queue and executes batch inserts to the DB.  
4. **Pruning Policy:** Implement a startup routine to check DB size and archive/delete old records if limits are exceeded.

## **Phase 2: Insight & Analysis (Priority P1)**

*Focus: Improving the quality of the data and the user's ability to interpret it.*

### **SN-02: Heuristic Reassembly**

**Goal:** Recover valid frames from fragmented OS serial streams.

1. **Circular Buffer:** Implement a high-performance byte buffer to store incoming chunks from the serial driver.  
2. **Frame Hunter Algorithm:** Implement the logic to scan the buffer for potential Slave IDs, look up the Function Code to predict length, and speculatively check CRCs.  
3. **Window Sliding:** Implement the pointer logic to discard processed bytes or advance by 1 byte on CRC failure to retry.

### **SN-03: Timing Compensation**

**Goal:** Accurate packet delineation on non-RTOS systems.

1. **Baud Rate Math:** Create a utility to calculate $t\_{3.5}$ based on the configured baud rate.  
2. **Driver Configuration:** Map the calculated time to the inter\_byte\_timeout parameter in pyserial.  
3. **Software-Side Buffer:** Implement a "Gap Detection" logic in the asyncio reader that treats a read timeout as a logical End-Of-Frame.

### **DA-01: Simultaneous Decode**

**Goal:** Solving the "Endianness Nightmare."

1. **Permutation Logic:** Create a utility function that takes 4 bytes (A, B, C, D) and returns all 4 permutations: ABCD (Big), DCBA (Little), CDAB (Mid-Big), BADC (Mid-Little).  
2. **Model Update:** Update the GUI's QAbstractTableModel to calculate these 4 values on-the-fly for the selected row.  
3. **UI Columns:** Add hidden/toggleable columns to the data grid for these alternate views.

### **WR-02: Write Intent Model**

**Goal:** Handling complex data types (Floats) in write operations.

1. **Splitter Logic:** Implement the math to convert a Python float into two 16-bit integers based on the target device's Endianness.  
2. **Atomic Write Construction:** Ensure these two registers are wrapped in a single Function Code 16 (Write Multiple Registers) request, rather than two FC06 requests, to ensure atomicity.  
3. **Endian Configuration:** Add UI/CLI flags to specify the target endianness for the write operation.

### **LOG-03: Session Replay**

**Goal:** Realistic simulation of past traffic.

1. **Replay Engine Class:** Create a class that accepts a time range and queries the SQLite DB.  
2. **Delta Calculator:** Implement the loop that calculates delay \= current\_packet.timestamp \- previous\_packet.timestamp.  
3. **Drift Correction:** Implement a periodic check against wall-clock time to adjust delay values if the Python sleep() is drifting.  
4. **Injection Interface:** Connect the Replay Engine to the Data Analysis pipeline so replayed packets appear in the GUI as if they were live.

## **Phase 3: Advanced Tools & Automation (Priority P2)**

*Focus: Specialized features for power users, testers, and integrators.*

### **FN-04: Error Injection**

**Goal:** Active fuzzing of slave devices.

1. **Interceptor Hook:** Add a hook in the Transport send\_pdu method that can modify the buffer before transmission.  
2. **CRC Corruptor:** Implement the logic to XOR the last 2 bytes of the frame.  
3. **Timing Injector:** Implement a send\_fragmented method that sends the header, sleeps (violating $t\_{1.5}$), and then sends the payload.

### **DA-03: Heuristic Scanning**

**Goal:** Faster network discovery.

1. **Probe Implementation:** Create a specialized request packet for Function Code 43 (Read Device ID).  
2. **Priority Queue:** Modify the scanner iterator to check IDs \[1, 2, 127, 247\] before iterating 3-246.  
3. **Parallelism (TCP):** Implement asyncio.gather to spawn 255 simultaneous connection attempts when scanning a /24 subnet, rather than sequential loops.

### **DA-04: PCAP Export**

**Goal:** Wireshark interoperability.

1. **Global Header Writer:** Implement binary writing of the PCAP Global Header (Magic Number, Version, DLT).  
2. **Pseudo-Header Encapsulation:** Implement the logic to wrap raw RTU bytes in a fake Ethernet/IP/TCP header or a User DLT header so Wireshark accepts it.  
3. **Export Stream:** Create a generator that reads from SQLite and yields PCAP-formatted byte blocks to a file handle.

### **WR-03: Macro System**

**Goal:** User-defined automation sequences.

1. **Macro Schema:** Define a JSON structure for saving sequences (\[{"step": 1, "action": "write", "address": 4001, "value": 50}, ...\]).  
2. **Recorder:** Add a "Record" toggle in the GUI that saves all manual user Write Intents to a temporary list.  
3. **Runner:** Implement a playback engine that executes the JSON steps, respecting defined wait times between steps.