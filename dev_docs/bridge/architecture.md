# **Architecture and Strategy for the UMDT \- Bridge**

## **Executive Summary**

The **UMDT \- Bridge** is an advanced middleware solution designed to sit transparently between a Modbus Master (e.g., SCADA, HMI, Cloud Gateway) and a Modbus Slave (e.g., PLC, Sensor, VFD). Part of the **Universal Modbus Diagnostic Tool (UMDT)** suite, this specific tool functions not just as a protocol translator, but as an intelligent **Interceptor and Relay Engine**.

The primary purpose of the Bridge is to decouple the physical communication layer from the logical data layer. By intercepting Modbus traffic in transit, the Bridge enables four critical capabilities that legacy devices lack:

1. **Protocol Agnosticism:** Bridging disparate interfaces (e.g., a Modbus TCP Master controlling a Modbus RTU Slave over RS-485).  
2. **Logic Injection:** A scripting layer that can modify payloads, block commands, or automate sequences without touching the firmware of the Master or Slave.  
3. **IIoT Telemetry:** A "Sidecar" observer that publishes real-time register values to MQTT brokers.  
4. **Forensic Logging:** A traffic recorder that captures raw byte streams into standard PCAP files for deep analysis in Wireshark.

## **1\. System Architecture: The Pipeline Model**

The UMDT \- Bridge architecture is modeled as a bi-directional processing pipeline. Traffic flows through a series of stages, allowing for inspection and modification at each step.

### **1.1 The Upstream Interface (Server Side)**

This interface faces the Master Device (SCADA/HMI).

* **Role:** Acts as a Modbus Server (Slave).  
* **Behavior:** Accepts incoming requests (e.g., Read Holding Registers 40001, Count 10).  
* **Transport:** Configurable as Modbus TCP (Port 502\) or Modbus RTU (Virtual Serial Port).

### **1.2 The Downstream Interface (Client Side)**

This interface faces the Slave Device (PLC/Sensor).

* **Role:** Acts as a Modbus Client (Master).  
* **Behavior:** Forwards the (potentially modified) request to the physical hardware.  
* **Transport:** Configurable as Modbus RTU (Physical RS-485) or Modbus TCP.

### **1.3 The Middleware Pipeline**

Connecting the Upstream and Downstream interfaces is the Middleware Pipeline. This is an asyncio-driven event loop that passes Request and Response objects through a chain of "Hooks."

Pipeline Entry Points:  
The pipeline can be triggered by two distinct sources:

1. **Event-Driven (Ingress):** Triggered by an incoming request from the Upstream Master.  
2. **Time-Driven (Periodic):** Triggered by an internal interval timer, allowing the Bridge to initiate actions independently.

**The Hook Chain:**

1. **Ingress Hook:** Triggered when a request is received from Upstream.  
2. **Periodic Hook:** Triggered on a schedule (e.g., every 1000ms).  
3. **Transformation Hook:** Python logic that can modify the request (e.g., remapping address 40001 to 40101).  
4. **Egress Hook:** Triggered before the request is sent Downstream.  
5. **Response Hook:** Triggered when the Downstream device replies, before the answer is relayed Upstream.

## **2\. Core Functional Modules**

### **2.1 The Soft-Gateway (RTU-to-TCP Bridge)**

This is the most common operational mode, solving the "Ethernet Gap" for legacy devices.

* **Scenario:** A modern SCADA system (TCP-only) needs to talk to a 20-year-old Generator Controller (RS-485 only).  
* **Bridge Implementation:**  
  * **Upstream:** Starts a TCP Server on 0.0.0.0:502.  
  * **Downstream:** Opens COM3 at 9600-8-N-1.  
  * **Logic:** The Bridge strips the MBAP (TCP) header, calculates the CRC16, and transmits the RTU frame. When the serial response arrives, it validates the CRC, wraps the data in an MBAP header, and replies to the SCADA system.  
* **Concurrency:** The Bridge maintains a thread-safe queue for the serial port, allowing multiple TCP clients to query the same serial bus without collision.

### **2.2 The Scriptable Logic Engine**

Commissioning often requires ad-hoc logic that is difficult to implement in rigid SCADA software or locked PLC firmware. The Bridge embeds a Python sandbox to run user scripts.

**Use Case: Safety Interlock (Event-Driven)**

* *Requirement:* Prevent writing a "Start" command to a motor if a specific condition isn't met.  
* *Implementation:*  
  def on\_request(req, context):  
      if req.function\_code \== 6 and req.address \== 100 (Start Cmd):  
          if context.state.get('SYSTEM\_READY') is False:  
               return ExceptionResponse(0x02) \# Illegal Data Access  
      return req \# Pass through allowed

### **2.3 The IIoT Telemetry Sidecar (MQTT)**

Traditional monitoring requires "Double Polling"—once for SCADA, once for the Cloud. This doubles the bus load and causes collisions. The Bridge solves this by "Snooping" on the active traffic.

* **Mechanism:**  
  1. SCADA asks for "Voltage".  
  2. Bridge relays request to Meter.  
  3. Meter replies "240V".  
  4. Bridge relays "240V" to SCADA.  
  5. **Simultaneously:** Bridge parses "240V" and publishes {"voltage": 240} to an MQTT broker.  
* **Result:** The Cloud gets real-time data with **Zero additional overhead** on the Modbus network.

### **2.4 The Forensic Logging Sidecar (PCAP)**

To diagnose complex integration issues, engineers often need to see "what goes in" versus "what comes out." The Bridge includes a dual-stream PCAP recorder.

* **Dual-Stream Capability:**  
  * **Upstream Log (upstream.pcap):** Captures traffic between the Master (SCADA) and the Bridge. This reveals what the SCADA *thinks* it is asking for.  
  * **Downstream Log (downstream.pcap):** Captures traffic between the Bridge and the Slave (PLC). This reveals what was *actually sent* to the wire (after any Logic Engine modifications).  
* **Encapsulation:**  
  * For TCP interfaces, standard Ethernet headers are logged.  
  * For RTU (Serial) interfaces, the logger wraps raw bytes in a pseudo-header (LinkType 147 or User DLT) allowing Wireshark to recognize and dissect the Modbus RTU frames.  
* **Use Case:**  
  * Verifying that a "Transformation Hook" is correctly remapping registers.  
  * Proving to a vendor that their SCADA system is sending malformed packets before they reach the device.

## **3\. Configuration & Tagging Strategy**

To make the Bridge data-aware, it utilizes the **Device Profile** system (shared with the core UMDT tools).

### **3.1 Device Profiles (YAML/JSON)**

Instead of hardcoding register addresses in Python scripts, users define profiles:

device: "PowerMeter\_X200"  
tags:  
  \- name: "Voltage\_L1"  
    address: 40001  
    type: "float32"  
    mqtt\_topic: "power/l1/voltage"  
  \- name: "Frequency"  
    address: 40010  
    type: "uint16"  
    scale: 0.1  
logging:  
  upstream\_pcap: "/var/log/umdt/upstream.pcap"  
  downstream\_pcap: "/var/log/umdt/downstream.pcap"  
  rotation: "daily"

### **3.2 The Tag Engine**

The Bridge automatically loads these profiles. When traffic passes through:

1. It checks if the address matches a known Tag.  
2. If matched, it applies the defined decoding (Type/Scale).  
3. It attaches the human-readable metadata (Name, Value, Units) to the internal event bus for Logging and MQTT publishing.

## **4\. Operational Mode: Transparent Pass-through**

The Bridge operates primarily in **Transparent Mode**, prioritizing data integrity and minimal latency.

* **Function:** Pure forwarding with optional inspection/modification.  
* **Latency:** Minimal (\< 2ms overhead).  
* **Behavior:**  
  1. **Receive:** Packet arrives from Master.  
  2. **Inspect:** Hooks allow for logging, logic checks, or modification.  
  3. **Forward:** Packet is sent to Slave.  
  4. **Reply:** Response traverses the path in reverse.  
* **Use Case:**  
  * Simple Protocol Conversion (TCP $\\leftrightarrow$ RTU).  
  * Adding MQTT monitoring to an existing control loop.  
  * Hot-fixing incorrect register maps without updating PLC firmware.

## **5\. Technical Stack**

* **Core:** Python 3.9+, asyncio.  
* **Modbus Stack:** pymodbus (Server and Client contexts).  
* **MQTT:** paho-mqtt (Async client).  
* **Config:** PyYAML / Pydantic.  
* **Logging:** Rich (Console) \+ SQLite (History).

## **6\. Conclusion**

The **UMDT \- Bridge** elevates the concept of a "Gateway" from a simple hardware dongle to a programmable intelligence layer. By empowering engineers to intercept, modify, and analyze traffic in flight, it resolves complex integration challenges—incompatible protocols, missing logic, and data silos—completely in software. It complements the UMDT Client and Server by serving as the permanent, deployed runtime solution for the problems identified during diagnosis.