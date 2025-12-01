import asyncio
import time
import datetime
from typing import List, Dict, Any, Optional
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QComboBox, 
    QPushButton, QTableView, QSplitter, QHeaderView, QTextEdit,
    QLabel, QAbstractItemView, QMessageBox
)
from PySide6.QtCore import Qt, QAbstractTableModel, Signal, QObject, QModelIndex
from PySide6.QtGui import QColor, QFont, QBrush

from umdt.core.sniffer import Sniffer
try:
    from serial.tools import list_ports
except ImportError:
    list_ports = None

class PacketTableModel(QAbstractTableModel):
    """Model for the traffic list."""
    
    COLUMNS = ["No.", "Time", "Slave", "FC", "Length", "Info"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._packets: List[Dict[str, Any]] = []

    def rowCount(self, parent=QModelIndex()):
        return len(self._packets)

    def columnCount(self, parent=QModelIndex()):
        return len(self.COLUMNS)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._packets)):
            return None
        
        packet = self._packets[index.row()]
        col = index.column()

        if role == Qt.DisplayRole:
            if col == 0: # No.
                return str(index.row() + 1)
            elif col == 1: # Time
                ts = packet['timestamp']
                dt = datetime.datetime.fromtimestamp(ts)
                return dt.strftime("%H:%M:%S.%f")[:-3]
            elif col == 2: # Slave
                raw = packet['raw']
                return str(raw[0]) if raw else "?"
            elif col == 3: # FC
                raw = packet['raw']
                return str(raw[1]) if len(raw) > 1 else "?"
            elif col == 4: # Length
                return str(len(packet['raw']))
            elif col == 5: # Info
                raw = packet['raw']
                valid = packet.get('valid_crc', False)
                status = "CRC OK" if valid else "CRC FAIL"
                hex_preview = " ".join(f"{b:02X}" for b in raw[:5])
                if len(raw) > 5:
                    hex_preview += "..."
                return f"[{status}] {hex_preview}"
        
        elif role == Qt.ForegroundRole:
            # Color code errors
            if not packet.get('valid_crc', True):
                return QBrush(Qt.red)
            
        elif role == Qt.TextAlignmentRole:
            if col in (0, 2, 3, 4):
                return Qt.AlignCenter
            return Qt.AlignLeft | Qt.AlignVCenter

        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self.COLUMNS[section]
        return None

    def add_packet(self, packet: Dict[str, Any]):
        self.beginInsertRows(QModelIndex(), len(self._packets), len(self._packets))
        self._packets.append(packet)
        self.endInsertRows()

    def get_packet(self, row: int) -> Optional[Dict[str, Any]]:
        if 0 <= row < len(self._packets):
            return self._packets[row]
        return None
    
    def clear(self):
        self.beginResetModel()
        self._packets.clear()
        self.endResetModel()


class SnifferWindow(QMainWindow):
    # Signal to bridge async callback to GUI thread
    packet_received = Signal(dict)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("UMDT Sniffer")
        self.resize(1000, 700)

        self.sniffer: Optional[Sniffer] = None
        self.is_running = False

        self.setup_ui()
        self.refresh_ports()
        
        # Connect signal
        self.packet_received.connect(self.on_packet_received)

    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # --- Top Control Bar ---
        control_layout = QHBoxLayout()
        
        control_layout.addWidget(QLabel("Port:"))
        self.combo_port = QComboBox()
        self.combo_port.setMinimumWidth(150)
        control_layout.addWidget(self.combo_port)
        
        refresh_btn = QPushButton("↻")
        refresh_btn.setFixedWidth(30)
        refresh_btn.setToolTip("Refresh Ports")
        refresh_btn.clicked.connect(self.refresh_ports)
        control_layout.addWidget(refresh_btn)

        control_layout.addSpacing(20)
        
        control_layout.addWidget(QLabel("Baud:"))
        self.combo_baud = QComboBox()
        self.combo_baud.addItems(["9600", "19200", "38400", "57600", "115200"])
        self.combo_baud.setCurrentText("9600")
        self.combo_baud.setEditable(True)
        control_layout.addWidget(self.combo_baud)

        control_layout.addSpacing(20)

        self.btn_start = QPushButton("Start Sniffing")
        self.btn_start.clicked.connect(self.toggle_sniffing)
        # self.btn_start.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold;")
        control_layout.addWidget(self.btn_start)
        
        self.btn_clear = QPushButton("Clear")
        self.btn_clear.clicked.connect(self.clear_log)
        control_layout.addWidget(self.btn_clear)

        control_layout.addStretch()
        main_layout.addLayout(control_layout)

        # --- Splitter (Table + Details) ---
        splitter = QSplitter(Qt.Vertical)
        main_layout.addWidget(splitter)

        # 1. Traffic Table
        self.table_view = QTableView()
        self.model = PacketTableModel()
        self.table_view.setModel(self.model)
        self.table_view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table_view.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table_view.verticalHeader().setVisible(False)
        self.table_view.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch) # Stretch info col
        self.table_view.selectionModel().selectionChanged.connect(self.on_selection_changed)
        
        # Font setup for table (monospace for hex ideally, but standard is fine)
        # font = QFont("Consolas", 9)
        # self.table_view.setFont(font)
        
        splitter.addWidget(self.table_view)

        # 2. Details Pane
        details_widget = QWidget()
        details_layout = QVBoxLayout(details_widget)
        details_layout.setContentsMargins(0, 0, 0, 0)
        
        lbl_details = QLabel("Packet Details:")
        lbl_details.setStyleSheet("font-weight: bold;")
        details_layout.addWidget(lbl_details)
        
        self.txt_details = QTextEdit()
        self.txt_details.setReadOnly(True)
        self.txt_details.setFont(QFont("Courier New", 10))
        details_layout.addWidget(self.txt_details)
        
        splitter.addWidget(details_widget)
        
        # Set initial splitter sizes
        splitter.setSizes([400, 200])

    def refresh_ports(self):
        self.combo_port.clear()
        if list_ports:
            ports = sorted(list_ports.comports(), key=lambda p: p.device)
            for p in ports:
                desc = f"{p.device} - {p.description}"
                self.combo_port.addItem(desc, userData=p.device)
        else:
            self.combo_port.addItem("No pyserial found")
            self.combo_port.setEnabled(False)

    def toggle_sniffing(self):
        if self.is_running:
            self.stop_sniffing()
        else:
            self.start_sniffing()

    def start_sniffing(self):
        port_idx = self.combo_port.currentIndex()
        if port_idx < 0:
            return
        port = self.combo_port.itemData(port_idx)
        if not port:
            return
            
        try:
            baud = int(self.combo_baud.currentText())
        except ValueError:
            QMessageBox.warning(self, "Invalid Baud", "Baud rate must be an integer.")
            return

        # Lock controls
        self.combo_port.setEnabled(False)
        self.combo_baud.setEnabled(False)
        self.btn_start.setText("Stop Sniffing")
        # self.btn_start.setStyleSheet("background-color: #F44336; color: white; font-weight: bold;")
        
        self.is_running = True
        
        # Init Sniffer
        # We pass a lambda that emits the QT signal to be thread-safe 
        # (even though qasync runs in same thread, good practice to decouple)
        self.sniffer = Sniffer(
            port=port, 
            baudrate=baud, 
            on_frame=lambda f: self.packet_received.emit(f)
        )
        
        # Schedule start
        asyncio.create_task(self._run_sniffer_start())

    async def _run_sniffer_start(self):
        try:
            if self.sniffer:
                await self.sniffer.start()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to start sniffer:\n{str(e)}")
            self.stop_sniffing()

    def stop_sniffing(self):
        self.is_running = False
        # Unlock controls
        self.combo_port.setEnabled(True)
        self.combo_baud.setEnabled(True)
        self.btn_start.setText("Start Sniffing")
        # self.btn_start.setStyleSheet("") # reset style

        if self.sniffer:
            asyncio.create_task(self._run_sniffer_stop())

    async def _run_sniffer_stop(self):
        if self.sniffer:
            await self.sniffer.stop()
            self.sniffer = None

    def closeEvent(self, event):
        """Handle window close event to ensure sniffer is stopped gracefully."""
        if self.sniffer and self.is_running:
            event.ignore()
            asyncio.create_task(self._async_close())
        else:
            event.accept()

    async def _async_close(self):
        """Async task to stop sniffer and then close the window."""
        await self._run_sniffer_stop()
        self.is_running = False
        self.close()

    def clear_log(self):
        self.model.clear()
        self.txt_details.clear()

    def on_packet_received(self, frame: dict):
        self.model.add_packet(frame)
        # Auto-scroll if at bottom? 
        self.table_view.scrollToBottom()

    def on_selection_changed(self, selected, deselected):
        indexes = self.table_view.selectionModel().selectedRows()
        if indexes:
            row = indexes[0].row()
            packet = self.model.get_packet(row)
            if packet:
                self.show_details(packet)
        else:
            self.txt_details.clear()

    def show_details(self, packet: dict):
        raw = packet['raw']
        ts = packet['timestamp']
        valid = packet.get('valid_crc', False)
        
        lines = []
        lines.append(f"Timestamp: {datetime.datetime.fromtimestamp(ts).isoformat()}")
        lines.append(f"Length:    {len(raw)} bytes")
        lines.append(f"CRC Check: {'PASS' if valid else 'FAIL'}")
        lines.append("-" * 40)
        
        # Hex Dump
        lines.append("Hex Dump:")
        
        # Format hex dump with 16 bytes per line + ASCII
        for i in range(0, len(raw), 16):
            chunk = raw[i:i+16]
            hex_part = " ".join(f"{b:02X}" for b in chunk)
            ascii_part = "".join((chr(b) if 32 <= b < 127 else ".") for b in chunk)
            # Pad hex part to align ASCII
            padding = "   " * (16 - len(chunk))
            lines.append(f"{i:04X}  {hex_part}{padding}  |{ascii_part}|")
            
        self.txt_details.setText("\n".join(lines))