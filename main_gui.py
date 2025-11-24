import sys
import os
import asyncio
from dataclasses import dataclass
from typing import List, Optional
from collections import deque
from datetime import datetime

from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QLineEdit,
    QComboBox,
    QTabWidget,
    QSizePolicy,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QMessageBox,
    QSpinBox,
)
from PySide6.QtGui import QIcon, QBrush, QColor
from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex
from PySide6.QtWidgets import QTextEdit
import qasync
from umdt.core.controller import CoreController
from serial.tools import list_ports
from urllib.parse import urlparse, parse_qs
from umdt.utils.ieee754 import from_bytes_to_float16
import logging
import inspect

# Configure logging; avoid enabling DEBUG globally which produces noisy third-party output
if not logging.getLogger().hasHandlers():
    logging.basicConfig(level=logging.INFO)
    # Quiet noisy third-party loggers by default
    logging.getLogger('asyncio').setLevel(logging.INFO)
    logging.getLogger('qasync').setLevel(logging.INFO)
    logging.getLogger('pymodbus').setLevel(logging.INFO)
logger = logging.getLogger("umdt.gui")


@dataclass
class ReadRow:
    index: str
    hex_value: str
    int_value: int
    float16: Optional[float]


@dataclass
class MonitorSample:
    """A single monitor poll sample (one interval)."""
    timestamp: str
    raw_registers: List[int]  # Raw 16-bit register values
    address_start: int
    unit_id: int
    error: Optional[str] = None


class MonitorModel(QAbstractTableModel):
    """Table model for monitor samples with one row per poll interval."""
    
    def __init__(self, max_samples: int = 1000):
        super().__init__()
        self._samples: deque = deque(maxlen=max_samples)
        self._max_samples = max_samples
        self._reg_count = 1
        self._decoding = "Signed"  # Hex, Unsigned, Signed, Float16
        self._long_mode = False
        self._endian = "big"

    def set_config(self, reg_count: int, long_mode: bool, endian: str):
        """Configure register count and decoding parameters."""
        self._reg_count = reg_count
        self._long_mode = long_mode
        self._endian = endian
        self.update_headers()

    def set_decoding(self, decoding: str):
        """Change decoding scheme and refresh all displayed data."""
        self._decoding = decoding
        # Refresh entire table view
        if len(self._samples) > 0:
            self.dataChanged.emit(
                self.index(0, 1),
                self.index(len(self._samples) - 1, self.columnCount() - 1)
            )

    def update_headers(self):
        """Regenerate column headers based on register count."""
        self.beginResetModel()
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # type: ignore[override]
        return len(self._samples)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # type: ignore[override]
        # Timestamp + one column per register value
        return 1 + self._reg_count

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):  # type: ignore[override]
        if not index.isValid():
            return None

        sample = self._samples[index.row()]
        col = index.column()

        if role == Qt.DisplayRole:
            # Column 0: timestamp
            if col == 0:
                return sample.timestamp

            # If error, show error in first value column and dashes in rest
            if sample.error is not None:
                if col == 1:
                    return f"ERROR: {sample.error}"
                else:
                    return "—"

            # Columns 1+: register values with selected decoding
            reg_idx = col - 1
            if reg_idx >= len(sample.raw_registers):
                return ""

            # Decode the register value based on current decoding mode
            reg_val = sample.raw_registers[reg_idx]

            if self._decoding == "Hex":
                return f"0x{reg_val:04X}"
            elif self._decoding == "Unsigned":
                return str(reg_val)
            elif self._decoding == "Signed":
                # Convert to signed int16
                signed = reg_val if reg_val < 0x8000 else reg_val - 0x10000
                return str(signed)
            elif self._decoding == "Float16":
                # Decode as float16
                try:
                    b = reg_val.to_bytes(2, byteorder="big", signed=False)
                    bb = b[::-1] if self._endian == "little" else b
                    f16 = from_bytes_to_float16(bb)
                    return f"{f16:.6g}" if f16 is not None else "—"
                except Exception:
                    return "—"

            return str(reg_val)

        if role == Qt.BackgroundRole:
            if sample.error is not None:
                return QBrush(QColor(0xFF, 0xE0, 0xE0))

        return None

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):  # type: ignore[override]
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            if section == 0:
                return "Timestamp"
            else:
                reg_num = section - 1
                return f"Reg{reg_num}"
        return super().headerData(section, orientation, role)

    def add_sample(self, sample: MonitorSample):
        """Add a new sample to the model (thread-safe via Qt signals)."""
        row = len(self._samples)
        self.beginInsertRows(QModelIndex(), row, row)
        self._samples.append(sample)
        self.endInsertRows()

    def clear_samples(self):
        """Clear all samples."""
        self.beginResetModel()
        self._samples.clear()
        self.endResetModel()


class ReadResultsModel(QAbstractTableModel):
    headers = ["Index", "Hex", "Int16", "Float16"]

    def __init__(self, rows: List[ReadRow]):
        super().__init__()
        self._rows = rows

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # type: ignore[override]
        return len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # type: ignore[override]
        return len(self.headers)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):  # type: ignore[override]
        if not index.isValid() or role != Qt.DisplayRole:
            return None
        row = self._rows[index.row()]
        col = index.column()
        if col == 0:
            return row.index
        if col == 1:
            return row.hex_value
        if col == 2:
            return str(row.int_value)
        if col == 3:
            return "" if row.float16 is None else str(row.float16)
        return None

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):  # type: ignore[override]
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.headers[section]
        return super().headerData(section, orientation, role)

    def update_rows(self, rows: List[ReadRow]):
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()


def encode_value_to_registers(
    value_text: str,
    long_mode: bool,
    endian: str,
    float_mode: bool,
    signed: bool,
) -> List[int]:
    """Encode a value into Modbus register list (16-bit ints).

    This consolidates the write encoding logic from the CLI for reuse.
    """
    import math
    import struct

    is_hex = value_text.strip().lower().startswith("0x")

    if float_mode:
        if is_hex:
            raise ValueError("Hex values are not allowed with float mode")
        try:
            float_val = float(value_text)
        except Exception:
            raise ValueError("Value must be a float when using float mode")
    else:
        try:
            int_val = int(value_text, 0)
        except Exception:
            raise ValueError("Value must be an integer or 0xHEX")

        if int_val < 0:
            signed = True

        if long_mode:
            if signed:
                if int_val < -0x80000000 or int_val > 0x7FFFFFFF:
                    raise ValueError("Value out of 32-bit signed range")
            else:
                if int_val < 0 or int_val > 0xFFFFFFFF:
                    raise ValueError("Value out of 32-bit unsigned range")
        else:
            if signed:
                if int_val < -0x8000 or int_val > 0x7FFF:
                    raise ValueError("Value out of 16-bit signed range")
            else:
                if int_val < 0 or int_val > 0xFFFF:
                    raise ValueError("Value out of 16-bit unsigned range")

    # Build register payload
    if float_mode:
        if long_mode:
            raw_be = struct.pack("!f", float_val)
            if endian == "big":
                bv = raw_be
            elif endian == "little":
                bv = raw_be[::-1]
            elif endian == "mid-big":
                bv = bytes([raw_be[2], raw_be[3], raw_be[0], raw_be[1]])
            else:  # mid-little
                bv = bytes([raw_be[1], raw_be[0], raw_be[3], raw_be[2]])
            regs = [int.from_bytes(bv[0:2], byteorder="big"), int.from_bytes(bv[2:4], byteorder="big")]
        else:
            # 16-bit float encoding (same as CLI)
            f = float_val
            if math.isnan(f):
                half = 0x7E00
            elif math.isinf(f):
                half = 0x7C00 if f > 0 else 0xFC00
            else:
                sign = 0
                if f < 0:
                    sign = 0x8000
                    f = -f
                if f == 0.0:
                    half = sign
                else:
                    exp = int(math.floor(math.log(f, 2)))
                    frac = f / (2 ** exp) - 1.0
                    exp16 = exp + 15
                    if exp16 <= 0:
                        mant16 = int(round(f / (2 ** -24)))
                        half = sign | mant16
                    elif exp16 >= 0x1F:
                        half = sign | 0x7C00
                    else:
                        mant16 = int(round(frac * (1 << 10)))
                        if mant16 == (1 << 10):
                            exp16 += 1
                            mant16 = 0
                            if exp16 >= 0x1F:
                                half = sign | 0x7C00
                            else:
                                half = sign | (exp16 << 10) | (mant16 & 0x3FF)
                        else:
                            half = sign | (exp16 << 10) | (mant16 & 0x3FF)

            b = half.to_bytes(2, byteorder="big")
            if endian == "little":
                b = b[::-1]
            regs = [int.from_bytes(b, byteorder="big")]
    else:
        width_bits = 32 if long_mode else 16
        if signed:
            max_val = 1 << width_bits
            int_u = int_val & (max_val - 1)
        else:
            int_u = int_val
        byte_len = width_bits // 8
        bv = int_u.to_bytes(byte_len, byteorder="big", signed=False)
        if long_mode:
            if endian == "big":
                pass
            elif endian == "little":
                bv = bv[::-1]
            elif endian == "mid-big":
                bv = bytes([bv[2], bv[3], bv[0], bv[1]])
            else:
                bv = bytes([bv[1], bv[0], bv[3], bv[2]])
            regs = [int.from_bytes(bv[0:2], byteorder="big"), int.from_bytes(bv[2:4], byteorder="big")]
        else:
            if endian == "little":
                bv = bv[::-1]
            regs = [int.from_bytes(bv, byteorder="big")]

    return regs


def run_gui_read(uri: str, address: int, count: int, long_mode: bool, endian: str, decode: bool) -> List[ReadRow]:
    """Blocking worker that performs a simple Modbus read for the GUI.

    For now this uses the first part of the URI to decide between serial and tcp
    and mirrors the basic CLI read behavior for 16-bit registers.
    """
    # lazy import to avoid top-level pymodbus dependency issues
    from urllib.parse import urlparse, parse_qs
    try:
        from pymodbus.client import ModbusTcpClient, ModbusSerialClient
    except Exception:
        from pymodbus.client.sync import ModbusTcpClient, ModbusSerialClient  # type: ignore

    def _read_holding_registers_compat(client, address: int, count: int, unit: int):
        fn = client.read_holding_registers
        try:
            sig = inspect.signature(fn)
            params = list(sig.parameters.keys())
        except Exception:
            params = []
        try:
            if 'count' in params and 'device_id' in params:
                return fn(address, count=count, device_id=unit)
            if 'count' in params and 'unit' in params:
                return fn(address, count=count, unit=unit)
            if 'count' in params and 'slave' in params:
                return fn(address, count=count, slave=unit)
            if 'count' in params and 'device' in params:
                return fn(address, count=count, device=unit)
            if 'count' in params and 'unit_id' in params:
                return fn(address, count=count, unit_id=unit)
        except TypeError:
            pass
        try:
            if 'count' in params:
                return fn(address, count=count)
        except Exception:
            pass
        try:
            return fn(address, count, unit)
        except Exception:
            try:
                return fn(address, count)
            except Exception:
                raise

    # debug logging removed for cleaner UI output
    parsed = urlparse(uri)
    scheme = parsed.scheme or "serial"
    qs = parse_qs(parsed.query or "")
    unit = int(qs.get("unit", ["1"])[0])

    if scheme == "serial":
        netloc = parsed.netloc or parsed.path.lstrip("/")
        if ":" in netloc:
            port, baud_s = netloc.split(":", 1)
            try:
                baud = int(baud_s)
            except Exception:
                baud = int(qs.get("baud", ["9600"])[0])
        else:
            port = netloc or qs.get("port", [""])[0]
            baud = int(qs.get("baud", ["9600"])[0])
        # serial parse debug removed
        client = ModbusSerialClient(port=port, baudrate=baud)
    else:
        host = parsed.hostname or "127.0.0.1"
        tcp_port = parsed.port or int(qs.get("port", ["502"])[0])
        client = ModbusTcpClient(host, port=tcp_port)

    if not client.connect():
        logger.error("run_gui_read: pymodbus client failed to connect (uri=%s)", uri)
        raise RuntimeError("Failed to connect")

    try:
        if long_mode:
            regs_to_read = max(1, count) * 2
        else:
            regs_to_read = max(1, count)

        rr = _read_holding_registers_compat(client, address, regs_to_read, unit)
        if not hasattr(rr, "registers"):
            logger.error("run_gui_read: read failed, response=%s", rr)
            raise RuntimeError("Read failed")
        regs = list(rr.registers)
        # received registers debug removed
    finally:
        client.close()

    # Decode results according to requested mode
    import struct

    # normalize 'all' to 'big' for now
    e_norm = endian if endian != "all" else "big"

    rows: List[ReadRow] = []
    if long_mode and decode:
        # combine pairs into 32-bit values
        for i in range(max(1, count)):
            ri = i * 2
            if ri + 1 >= len(regs):
                break
            r1 = regs[ri]
            r2 = regs[ri + 1]
            b1 = int(r1).to_bytes(2, byteorder="big", signed=False)
            b2 = int(r2).to_bytes(2, byteorder="big", signed=False)
            bv = b1 + b2

            # apply same transform used in encoding (self-inverse)
            if e_norm == "big":
                raw_be = bv
            elif e_norm == "little":
                raw_be = bv[::-1]
            elif e_norm == "mid-big":
                raw_be = bytes([bv[2], bv[3], bv[0], bv[1]])
            else:  # mid-little
                raw_be = bytes([bv[1], bv[0], bv[3], bv[2]])

            try:
                i32 = int.from_bytes(raw_be, byteorder="big", signed=True)
            except Exception:
                i32 = 0
            try:
                f32 = struct.unpack("!f", raw_be)[0]
            except Exception:
                f32 = None

            hexv = "0x" + bv.hex().upper()
            rows.append(ReadRow(index=str(address + ri), hex_value=hexv, int_value=i32, float16=f32))
        return rows

    # 16-bit decode (or non-decode fallback)
    for i, r in enumerate(regs):
        idx_disp = str(address + i)
        b = int(r).to_bytes(2, byteorder="big", signed=False)
        hexv = "0x" + b.hex().upper()

        # apply endian for single-register interpretations
        bb = b[::-1] if e_norm == "little" else b

        # signed int16
        u = int.from_bytes(bb, byteorder="big", signed=False)
        i16 = u if u < 0x8000 else u - 0x10000
        try:
            f16 = from_bytes_to_float16(bb)
        except Exception:
            f16 = None
        rows.append(ReadRow(index=idx_disp, hex_value=hexv, int_value=i16, float16=f16))
    return rows


def run_gui_write(
    uri: str,
    address: int,
    long_mode: bool,
    endian: str,
    float_mode: bool,
    signed: bool,
    value_text: str,
) -> tuple[bool, str]:
    """Blocking worker that performs a simple Modbus write for the GUI.

    This mirrors a subset of the CLI write validation and encoding logic.
    """
    import math
    import struct
    from urllib.parse import urlparse, parse_qs
    try:
        from pymodbus.client import ModbusTcpClient, ModbusSerialClient
    except Exception:
        from pymodbus.client.sync import ModbusTcpClient, ModbusSerialClient  # type: ignore

    def _write_registers_compat(client, address: int, regs, unit: int):
        fn = getattr(client, 'write_registers')
        try:
            sig = inspect.signature(fn)
            params = list(sig.parameters.keys())
        except Exception:
            params = []
        try:
            if 'unit' in params:
                return fn(address, regs, unit=unit)
            if 'slave' in params:
                return fn(address, regs, slave=unit)
        except TypeError:
            pass
        try:
            return fn(address, regs)
        except Exception:
            return fn(address, regs, unit)

    def _write_register_compat(client, address: int, val, unit: int):
        fn = getattr(client, 'write_register')
        try:
            sig = inspect.signature(fn)
            params = list(sig.parameters.keys())
        except Exception:
            params = []
        try:
            if 'unit' in params:
                return fn(address, val, unit=unit)
            if 'slave' in params:
                return fn(address, val, slave=unit)
        except TypeError:
            pass
        try:
            return fn(address, val)
        except Exception:
            return fn(address, val, unit)

    # Parse and validate value
    is_hex = value_text.strip().lower().startswith("0x")

    if float_mode:
        if is_hex:
            raise ValueError("Hex values are not allowed with float mode")
        try:
            float_val = float(value_text)
        except Exception:
            raise ValueError("Value must be a float when using float mode")
    else:
        try:
            int_val = int(value_text, 0)
        except Exception:
            raise ValueError("Value must be an integer or 0xHEX")

        if int_val < 0:
            signed = True

        if long_mode:
            if signed:
                if int_val < -0x80000000 or int_val > 0x7FFFFFFF:
                    raise ValueError("Value out of 32-bit signed range")
            else:
                if int_val < 0 or int_val > 0xFFFFFFFF:
                    raise ValueError("Value out of 32-bit unsigned range")
        else:
            if signed:
                if int_val < -0x8000 or int_val > 0x7FFF:
                    raise ValueError("Value out of 16-bit signed range")
            else:
                if int_val < 0 or int_val > 0xFFFF:
                    raise ValueError("Value out of 16-bit unsigned range")

    # Build register payload
    if float_mode:
        if long_mode:
            raw_be = struct.pack("!f", float_val)
            if endian == "big":
                bv = raw_be
            elif endian == "little":
                bv = raw_be[::-1]
            elif endian == "mid-big":
                bv = bytes([raw_be[2], raw_be[3], raw_be[0], raw_be[1]])
            else:  # mid-little
                bv = bytes([raw_be[1], raw_be[0], raw_be[3], raw_be[2]])
            regs = [int.from_bytes(bv[0:2], byteorder="big"), int.from_bytes(bv[2:4], byteorder="big")]
        else:
            # 16-bit float encoding (same as CLI)
            f = float_val
            if math.isnan(f):
                half = 0x7E00
            elif math.isinf(f):
                half = 0x7C00 if f > 0 else 0xFC00
            else:
                sign = 0
                if f < 0:
                    sign = 0x8000
                    f = -f
                if f == 0.0:
                    half = sign
                else:
                    exp = int(math.floor(math.log(f, 2)))
                    frac = f / (2 ** exp) - 1.0
                    exp16 = exp + 15
                    if exp16 <= 0:
                        mant16 = int(round(f / (2 ** -24)))
                        half = sign | mant16
                    elif exp16 >= 0x1F:
                        half = sign | 0x7C00
                    else:
                        mant16 = int(round(frac * (1 << 10)))
                        if mant16 == (1 << 10):
                            exp16 += 1
                            mant16 = 0
                            if exp16 >= 0x1F:
                                half = sign | 0x7C00
                            else:
                                half = sign | (exp16 << 10) | (mant16 & 0x3FF)
                        else:
                            half = sign | (exp16 << 10) | (mant16 & 0x3FF)

            b = half.to_bytes(2, byteorder="big")
            if endian == "little":
                b = b[::-1]
            regs = [int.from_bytes(b, byteorder="big")]
    else:
        width_bits = 32 if long_mode else 16
        if signed:
            max_val = 1 << width_bits
            int_u = int_val & (max_val - 1)
        else:
            int_u = int_val
        byte_len = width_bits // 8
        bv = int_u.to_bytes(byte_len, byteorder="big", signed=False)
        if long_mode:
            if endian == "big":
                pass
            elif endian == "little":
                bv = bv[::-1]
            elif endian == "mid-big":
                bv = bytes([bv[2], bv[3], bv[0], bv[1]])
            else:
                bv = bytes([bv[1], bv[0], bv[3], bv[2]])
            regs = [int.from_bytes(bv[0:2], byteorder="big"), int.from_bytes(bv[2:4], byteorder="big")]
        else:
            if endian == "little":
                bv = bv[::-1]
            regs = [int.from_bytes(bv, byteorder="big")]

    parsed = urlparse(uri)
    scheme = parsed.scheme or "serial"
    qs = parse_qs(parsed.query or "")
    unit = int(qs.get("unit", ["1"])[0])

    if scheme == "serial":
        netloc = parsed.netloc or parsed.path.lstrip("/")
        if ":" in netloc:
            port, baud_s = netloc.split(":", 1)
            try:
                baud = int(baud_s)
            except Exception:
                baud = int(qs.get("baud", ["9600"])[0])
        else:
            port = netloc or qs.get("port", [""])[0]
            baud = int(qs.get("baud", ["9600"])[0])
        client = ModbusSerialClient(port=port, baudrate=baud)
    else:
        host = parsed.hostname or "127.0.0.1"
        tcp_port = parsed.port or int(qs.get("port", ["502"])[0])
        client = ModbusTcpClient(host, port=tcp_port)

    if not client.connect():
        raise RuntimeError("Failed to connect")

    try:
        if long_mode or (float_mode and len(regs) == 2):
            res = _write_registers_compat(client, address, regs, unit)
        else:
            res = _write_register_compat(client, address, regs[0], unit)
        if hasattr(res, "isError") and res.isError():
            return False, "Write failed"
        return True, "Write OK"
    finally:
        client.close()

# project icon (placed next to main scripts)
ICON_PATH = os.path.join(os.path.dirname(__file__), "umdt.ico")

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("UMDT")
        self.resize(900, 600)

        # Central widget with vertical layout
        central = QWidget(self)
        self.setCentralWidget(central)
        root_layout = QVBoxLayout()
        central.setLayout(root_layout)

        # --- Connection panel (shared across tabs) ---
        conn_row = QHBoxLayout()

        self.conn_type_combo = QComboBox()
        self.conn_type_combo.addItems(["Serial", "TCP"])
        conn_row.addWidget(QLabel("Connection:"))
        conn_row.addWidget(self.conn_type_combo)

        # Serial widgets grouped into a single expanding widget
        self.port_label = QLabel("Port:")
        self.serial_port_combo = QComboBox()
        self.serial_port_combo.setEditable(True)

        self.baud_label = QLabel("Baud:")
        self.baud_edit = QLineEdit("115200")

        self.serial_widget = QWidget()
        serial_layout = QHBoxLayout()
        serial_layout.setContentsMargins(0, 0, 0, 0)
        serial_layout.addWidget(self.port_label)
        serial_layout.addWidget(self.serial_port_combo)
        serial_layout.addWidget(self.baud_label)
        serial_layout.addWidget(self.baud_edit)
        self.serial_widget.setLayout(serial_layout)
        self.serial_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        # TCP widgets grouped into a single expanding widget
        self.host_label = QLabel("Host:")
        self.host_edit = QLineEdit()
        self.host_edit.setPlaceholderText("192.168.0.10")
        self.tcp_port_label = QLabel("TCP Port:")
        self.tcp_port_edit = QLineEdit("502")

        self.tcp_widget = QWidget()
        tcp_layout = QHBoxLayout()
        tcp_layout.setContentsMargins(0, 0, 0, 0)
        tcp_layout.addWidget(self.host_label)
        tcp_layout.addWidget(self.host_edit)
        tcp_layout.addWidget(self.tcp_port_label)
        tcp_layout.addWidget(self.tcp_port_edit)
        self.tcp_widget.setLayout(tcp_layout)
        self.tcp_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        # Add the grouped widgets to the connection row so they take available space
        conn_row.addWidget(self.serial_widget)
        conn_row.addWidget(self.tcp_widget)

        # Unit and controls
        self.unit_label = QLabel("Unit:")
        self.unit_edit = QLineEdit("1")
        self.unit_edit.setMaximumWidth(60)
        conn_row.addWidget(self.unit_label)
        conn_row.addWidget(self.unit_edit)

        self.btn_connect = QPushButton("Connect")
        self.btn_connect.clicked.connect(self.on_connect_clicked)
        conn_row.addWidget(self.btn_connect)

        self.status_label = QLabel("Disconnected")
        conn_row.addWidget(self.status_label)

        root_layout.addLayout(conn_row)

        # Wire up connection type change to toggle serial/TCP inputs
        self.conn_type_combo.currentIndexChanged.connect(self.on_conn_type_changed)
        # Populate serial ports now
        self.refresh_serial_ports()
        # Initialize visibility
        self.on_conn_type_changed(self.conn_type_combo.currentIndex())

        # --- Tabs ---
        self.tabs = QTabWidget()
        self.interact_tab = QWidget()
        self.monitor_tab = QWidget()
        self.tabs.addTab(self.interact_tab, "Interact")
        self.tabs.addTab(self.monitor_tab, "Monitor")

        # Build Interact tab layout (Read / Write panels)
        self._build_interact_tab()

        # Build Monitor tab layout
        self._build_monitor_tab()

        root_layout.addWidget(self.tabs)

        # Lightweight log view below tabs
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumHeight(160)
        root_layout.addWidget(self.log_view)

        # Initialize CoreController with placeholder URI; real URI will be
        # built from the connection panel when connecting.
        self.controller = CoreController(uri=None)
        # Async lock to prevent concurrent read operations
        self._read_lock = asyncio.Lock()
        # Monitor polling task
        self._monitor_task: Optional[asyncio.Task] = None
        # observe controller status/log entries to update UI live
        try:
            self.controller.add_observer(self._on_controller_entry)
        except Exception:
            pass

    # --- Intent helpers ---

    def _normalize_endian(self, raw: str, allow_all: bool = False) -> Optional[str]:
        e_str = (raw or "big").lower()
        mapping = {
            "b": "big",
            "big": "big",
            "l": "little",
            "little": "little",
            "mb": "mid-big",
            "mid-big": "mid-big",
            "ml": "mid-little",
            "mid-little": "mid-little",
        }
        if allow_all:
            mapping["all"] = "all"
        return mapping.get(e_str)

    def _parse_address(self, text: str) -> Optional[int]:
        if not text:
            return None
        try:
            return int(text.strip(), 0)
        except Exception:
            return None

    def _parse_int(self, text: str) -> Optional[int]:
        if not text:
            return None
        try:
            return int(text.strip(), 0)
        except Exception:
            return None

    def _build_interact_tab(self) -> None:
        layout = QVBoxLayout()

        # --- Read panel ---
        read_row = QHBoxLayout()
        self.read_addr_edit = QLineEdit()
        self.read_addr_edit.setPlaceholderText("Address (e.g. 1 or 0x0001)")
        self.read_count_edit = QLineEdit("1")
        self.read_long_checkbox = QPushButton("Long (32-bit)")
        self.read_long_checkbox.setCheckable(True)

        self.read_endian_combo = QComboBox()
        self.read_endian_combo.addItems(["big", "little", "mid-big", "mid-little", "all"])

        read_row.addWidget(QLabel("Addr:"))
        read_row.addWidget(self.read_addr_edit)
        read_row.addWidget(QLabel("Count:"))
        read_row.addWidget(self.read_count_edit)
        read_row.addWidget(self.read_long_checkbox)
        read_row.addWidget(QLabel("Endian:"))
        read_row.addWidget(self.read_endian_combo)

        # Single Read button (always performs decoded read)
        self.btn_read = QPushButton("Read")
        read_row.addWidget(self.btn_read)

        layout.addLayout(read_row)

        # Results table
        self.read_table = QTableView()
        self.read_model = ReadResultsModel([])
        self.read_table.setModel(self.read_model)
        # Hide vertical header (row numbers) to avoid duplicate index column
        try:
            self.read_table.verticalHeader().setVisible(False)
        except Exception:
            pass
        # Select whole rows and single selection for details panel
        try:
            self.read_table.setSelectionBehavior(QTableView.SelectRows)
            self.read_table.setSelectionMode(QTableView.SingleSelection)
            self.read_table.selectionModel().selectionChanged.connect(self.on_read_selection_changed)
        except Exception:
            pass
        header = self.read_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.read_table)

        # Details table for selected read row: show decoding across endianness
        # Use full set of columns so 32-bit longs can display 32-bit interpretations
        self.read_details_table = QTableWidget()
        self.read_details_table.setColumnCount(9)
        self.read_details_table.setHorizontalHeaderLabels(["Format", "Hex", "UInt16", "Int16", "Float16", "Hex32", "UInt32", "Int32", "Float32"])
        self.read_details_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.read_details_table.setMaximumHeight(160)
        layout.addWidget(self.read_details_table)

        # --- Write panel ---
        write_row = QHBoxLayout()
        self.write_addr_edit = QLineEdit()
        self.write_addr_edit.setPlaceholderText("Address (e.g. 1 or 0x0001)")

        self.write_long_checkbox = QPushButton("Long (32-bit)")
        self.write_long_checkbox.setCheckable(True)

        self.write_endian_combo = QComboBox()
        self.write_endian_combo.addItems(["big", "little", "mid-big", "mid-little"])

        self.write_float_checkbox = QPushButton("Float")
        self.write_float_checkbox.setCheckable(True)

        self.write_signed_checkbox = QPushButton("Signed")
        self.write_signed_checkbox.setCheckable(True)

        self.write_value_edit = QLineEdit()
        self.write_value_edit.setPlaceholderText("Value (int/0xHEX or float)")

        self.btn_write = QPushButton("Write")

        write_row.addWidget(QLabel("Addr:"))
        write_row.addWidget(self.write_addr_edit)
        write_row.addWidget(self.write_long_checkbox)
        write_row.addWidget(QLabel("Endian:"))
        write_row.addWidget(self.write_endian_combo)
        write_row.addWidget(self.write_float_checkbox)
        write_row.addWidget(self.write_signed_checkbox)
        write_row.addWidget(QLabel("Value:"))
        write_row.addWidget(self.write_value_edit)
        write_row.addWidget(self.btn_write)

        layout.addLayout(write_row)

        # Write status label
        self.write_status_label = QLabel("")
        layout.addWidget(self.write_status_label)

        self.interact_tab.setLayout(layout)

        # Wire the single Read button
        self.btn_read.clicked.connect(self.on_read_clicked)
        self.btn_write.clicked.connect(self.on_write_clicked)

    def _build_monitor_tab(self) -> None:
        """Build the Monitor tab UI with polling controls and scrolling table."""
        layout = QVBoxLayout()

        # --- Monitor configuration panel ---
        config_row = QHBoxLayout()

        self.monitor_addr_edit = QLineEdit()
        self.monitor_addr_edit.setPlaceholderText("Address (e.g. 0 or 0x0000)")
        self.monitor_addr_edit.setText("0")

        self.monitor_count_edit = QLineEdit("1")
        self.monitor_count_edit.setMaximumWidth(60)

        self.monitor_long_checkbox = QPushButton("Long (32-bit)")
        self.monitor_long_checkbox.setCheckable(True)

        self.monitor_endian_combo = QComboBox()
        self.monitor_endian_combo.addItems(["big", "little", "mid-big", "mid-little"])

        self.monitor_interval_spin = QSpinBox()
        self.monitor_interval_spin.setMinimum(100)
        self.monitor_interval_spin.setMaximum(60000)
        self.monitor_interval_spin.setValue(1000)
        self.monitor_interval_spin.setSuffix(" ms")
        self.monitor_interval_spin.setMaximumWidth(100)

        config_row.addWidget(QLabel("Addr:"))
        config_row.addWidget(self.monitor_addr_edit)
        config_row.addWidget(QLabel("Count:"))
        config_row.addWidget(self.monitor_count_edit)
        config_row.addWidget(self.monitor_long_checkbox)
        config_row.addWidget(QLabel("Endian:"))
        config_row.addWidget(self.monitor_endian_combo)
        config_row.addWidget(QLabel("Interval:"))
        config_row.addWidget(self.monitor_interval_spin)

        self.btn_monitor_start = QPushButton("Start")
        self.btn_monitor_stop = QPushButton("Stop")
        self.btn_monitor_stop.setEnabled(False)
        self.btn_monitor_clear = QPushButton("Clear")

        config_row.addWidget(self.btn_monitor_start)
        config_row.addWidget(self.btn_monitor_stop)
        config_row.addWidget(self.btn_monitor_clear)
        config_row.addStretch()

        layout.addLayout(config_row)

        decode_row = QHBoxLayout()
        decode_row.addWidget(QLabel("Display as:"))
        self.monitor_decode_combo = QComboBox()
        self.monitor_decode_combo.addItems(["Hex", "Unsigned", "Signed", "Float16"])
        self.monitor_decode_combo.setCurrentText("Signed")
        self.monitor_decode_combo.currentTextChanged.connect(self.on_monitor_decode_changed)
        decode_row.addWidget(self.monitor_decode_combo)
        decode_row.addStretch()
        layout.addLayout(decode_row)

        # --- Monitor results table ---
        self.monitor_table = QTableView()
        self.monitor_model = MonitorModel(max_samples=1000)
        self.monitor_table.setModel(self.monitor_model)
        # Hide vertical header (row numbers)
        try:
            self.monitor_table.verticalHeader().setVisible(False)
        except Exception:
            pass
        # Select whole rows for monitor and connect selection changed
        try:
            self.monitor_table.setSelectionBehavior(QTableView.SelectRows)
            self.monitor_table.setSelectionMode(QTableView.SingleSelection)
            self.monitor_table.selectionModel().selectionChanged.connect(self.on_monitor_selection_changed)
        except Exception:
            pass
        header = self.monitor_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.monitor_table)
        # Status label
        self.monitor_status_label = QLabel("Idle")
        layout.addWidget(self.monitor_status_label)

        # Details table for selected monitor row: show decoding across endianness
        self.monitor_details_table = QTableWidget()
        self.monitor_details_table.setColumnCount(9)
        self.monitor_details_table.setHorizontalHeaderLabels(["Format", "Hex", "UInt16", "Int16", "Float16", "Hex32", "UInt32", "Int32", "Float32"])
        self.monitor_details_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.monitor_details_table.setMaximumHeight(200)
        layout.addWidget(self.monitor_details_table)

        self.monitor_tab.setLayout(layout)

        # Wire buttons
        self.btn_monitor_start.clicked.connect(self.on_monitor_start_clicked)
        self.btn_monitor_stop.clicked.connect(self.on_monitor_stop_clicked)
        self.btn_monitor_clear.clicked.connect(self.on_monitor_clear_clicked)
        # Selection handlers for details panels
        try:
            self.read_table.selectionModel().selectionChanged.connect(self.on_read_selection_changed)
        except Exception:
            pass
        try:
            self.monitor_table.selectionModel().selectionChanged.connect(self.on_monitor_selection_changed)
        except Exception:
            pass

    def build_uri(self) -> str:
        """Build a connection URI from the connection panel fields.

        This is a simple placeholder; later steps can refine this to match
        the ConnectionManager URI scheme (e.g. serial://, tcp://, mock://).
        """
        conn_type = self.conn_type_combo.currentText().lower()
        unit = self.unit_edit.text().strip() or "1"
        if conn_type == "serial":
            # Prefer the actual device stored in itemData if present
            idx = self.serial_port_combo.currentIndex()
            dev = None
            if idx >= 0:
                dev = self.serial_port_combo.itemData(idx)
            port = (dev or self.serial_port_combo.currentText()).strip() or "COM5"
            baud = self.baud_edit.text().strip() or "115200"
            # ConnectionManager expects serial://PORT:BAUD
            return f"serial://{port}:{baud}?unit={unit}"
        else:
            host = self.host_edit.text().strip() or "127.0.0.1"
            tcp_port = self.tcp_port_edit.text().strip() or "502"
            return f"tcp://{host}:{tcp_port}?unit={unit}"

    @qasync.asyncSlot()
    async def on_connect_clicked(self):
        # Determine current connected state: either controller running or a direct serial URI set
        controller_running = getattr(self, 'controller', None) and getattr(self.controller, 'running', False)
        serial_direct = getattr(self, '_direct_serial_uri', None) is not None
        is_connected = bool(controller_running or serial_direct)

        if not is_connected:
            # reconfigure controller URI before starting
            uri = self.build_uri()
            conn_type = self.conn_type_combo.currentText().lower()

            # For serial connections prefer the blocking pymodbus client (CLI pattern)
            # rather than starting the async ConnectionManager which opens the serial
            # port with pyserial-asyncio (causes port sharing issues). We store the
            # serial URI and use the fallback read/write workers instead of starting
            # the controller.
            if conn_type == 'serial':
                self._direct_serial_uri = uri
                self.status_label.setText("Ready (serial)")
                self.btn_connect.setText("Disconnect")
            else:
                # recreate controller with new URI and start it
                self.controller = CoreController(uri=uri)
                try:
                    self.controller.add_observer(self._on_controller_entry)
                except Exception:
                    pass
                # start controller in background so UI doesn't block
                self.status_label.setText("Connecting...")
                self.btn_connect.setText("Disconnect")
                # create background task to start controller (does not block UI)
                self._start_task = asyncio.create_task(self.controller.start())

                # If using the ConnectionManager, wait briefly for the manager to signal connected
                if getattr(self.controller, '_use_manager', False) and getattr(self.controller, '_manager', None):
                    try:
                        await asyncio.wait_for(self.controller._manager._connected_event.wait(), timeout=0.5)
                        self.status_label.setText("Connected")
                    except Exception:
                        # leave status as Connecting...; manager will notify later via controller callbacks
                        pass
        else:
            self.status_label.setText("Disconnecting...")
            try:
                # if we used direct serial fallback, just clear that state
                if getattr(self, '_direct_serial_uri', None):
                    self._direct_serial_uri = None
                # stop controller if running
                if getattr(self.controller, 'running', False):
                    await self.controller.stop()
            finally:
                # Ensure UI shows disconnected state
                self.status_label.setText("Disconnected")
                self.btn_connect.setText("Connect")

    def _on_controller_entry(self, entry: dict):
        """Observer callback for controller entries (logs/status).

        Expects entries with 'direction' and 'data'. Update the status label
        when receiving STATUS notifications from ConnectionManager.
        """
        try:
            if not isinstance(entry, dict):
                return
            if entry.get("direction") == "STATUS":
                data = entry.get("data", "")
                # Update UI label; this is called from the asyncio loop integrated
                # with Qt via qasync, so direct widget updates are safe.
                try:
                    text = str(data)
                    self.status_label.setText(text)
                    # Color-code status: green for connected/ready, red for errors
                    s = text.lower()
                    if "connected" in s or "ready" in s or "ok" in s:
                        self.status_label.setStyleSheet("color: darkgreen")
                    elif "error" in s or "failed" in s or "connect error" in s:
                        self.status_label.setStyleSheet("color: darkred")
                    else:
                        self.status_label.setStyleSheet("")
                except Exception:
                    pass

                # If the manager signals a connect error, show a popup with guidance
                try:
                    s = str(data).lower()
                    # Append to log view for visibility
                    try:
                        self.log_view.append(f"STATUS: {s}")
                    except Exception:
                        pass
                    if "connect error" in s or "no module named" in s:
                        # show a warning with install hint if serial_asyncio missing
                        if "serial_async" in s or "serial_asyncio" in s:
                            QMessageBox.warning(
                                self,
                                "Connection error",
                                "Serial transport failed: 'pyserial-asyncio' is required.\nInstall with: pip install pyserial-asyncio",
                            )
                        else:
                            QMessageBox.warning(self, "Connection error", str(data))
                except Exception:
                    pass
        except Exception:
            pass

    def on_conn_type_changed(self, index: int | None = None):
        """Show/hide serial vs TCP input widgets based on the selected connection type."""
        text = self.conn_type_combo.currentText().lower()
        is_serial = text == 'serial'

        # Toggle grouped widgets visibility
        self.serial_widget.setVisible(is_serial)
        self.tcp_widget.setVisible(not is_serial)

        # Refresh serial port list when switching to Serial
        if is_serial:
            try:
                self.refresh_serial_ports()
            except Exception:
                pass

    def refresh_serial_ports(self):
        """Discover serial ports and populate the combo box."""
        self.serial_port_combo.clear()
        try:
            ports = list_ports.comports()
            for p in ports:
                # display device name (e.g., COM3) and description
                display = p.device
                if p.description:
                    display = f"{p.device} — {p.description}"
                self.serial_port_combo.addItem(display, userData=p.device)
        except Exception:
            ports = []

        # If no ports found, provide a sensible editable placeholder
        if not ports:
            # leave combo editable so user can type a port like COM5
            self.serial_port_combo.addItem("COM5")

    # --- Interact tab slots ---

    @qasync.asyncSlot()
    async def on_read_clicked(self):
        # Single read action: always perform decoded read
        await self._perform_read(decode=True)

    @qasync.asyncSlot()
    async def on_write_clicked(self):
        await self._perform_write()

    async def _perform_read(self, decode: bool) -> None:
        addr = self._parse_address(self.read_addr_edit.text())
        if addr is None:
            QMessageBox.warning(self, "Invalid address", "Enter a valid decimal or 0xHEX address for read.")
            return

        # Require explicit Connect action from the top-bar before allowing reads.
        # For serial connections the top-bar Connect sets `_direct_serial_uri`.
        controller_running = getattr(self, 'controller', None) and getattr(self.controller, 'running', False)
        serial_direct = getattr(self, '_direct_serial_uri', None) is not None
        if not (controller_running or serial_direct):
            QMessageBox.warning(self, "Not connected", "Please use the Connect button in the top bar to establish a connection before reading.")
            return

        # Prevent concurrent reads
        if self._read_lock.locked():
            QMessageBox.information(self, "Busy", "A read is already in progress.")
            return

        

        async with self._read_lock:
            # disable read button while active to avoid re-entrancy
            try:
                self.btn_read.setEnabled(False)
            except Exception:
                pass
            try:
                count = self._parse_int(self.read_count_edit.text()) or 1
                long_mode = self.read_long_checkbox.isChecked()
                e_norm = self._normalize_endian(self.read_endian_combo.currentText(), allow_all=True)
                if e_norm is None:
                    QMessageBox.warning(self, "Invalid endian", "Select a valid endian option.")
                    return

                # Get unit ID from connection panel
                unit = self._parse_int(self.unit_edit.text()) or 1

                # Determine register count to read
                regs_to_read = count * 2 if long_mode else count

                # read invocation debug removed

                # Check if we can use the running controller's transport
                if getattr(self, 'controller', None) and getattr(self.controller, 'running', False):
                    # Check if transport is actually connected
                    if self.controller._use_manager and self.controller._manager:
                        if not self.controller._manager._connected_event.is_set():
                            QMessageBox.warning(self, "Not connected", "Controller is starting; wait for connection to complete.")
                            return
                    # Use CoreController Modbus methods (shared transport)
                    try:
                        regs = await self.controller.modbus_read_holding_registers(unit, addr, regs_to_read)
                        if regs is None:
                            QMessageBox.critical(self, "Read error", "Modbus read failed (timeout or error response)")
                            return
                        if len(regs) == 0:
                            QMessageBox.information(self, "No registers", "Read returned no registers.")
                            self.read_model.update_rows([])
                            self.status_label.setText("No registers")
                            return
                    except Exception as exc:
                        logger.exception("_perform_read: controller read exception")
                        QMessageBox.critical(self, "Read error", str(exc))
                        return
                else:
                    # Fall back to standalone pymodbus client
                    uri = self.build_uri()
                    # Basic validation: ensure serial port or host present in URI
                    parsed = urlparse(uri)
                    scheme = parsed.scheme or 'serial'
                    if scheme == 'serial':
                        port = parsed.netloc or parsed.path.lstrip('/')
                        # strip optional baud portion if present
                        if ':' in port:
                            port = port.split(':', 1)[0]
                        if not port:
                            QMessageBox.warning(self, "Missing port", "Select or enter a serial port before reading.")
                            return
                    else:
                        host = parsed.hostname
                        if not host:
                            QMessageBox.warning(self, "Missing host", "Enter a TCP host before reading.")
                            return

                    # Run blocking Modbus read in thread to avoid blocking the GUI
                    # indicate activity
                    self.status_label.setText("Reading...")
                    try:
                        rows = await self._read_registers(uri, addr, count, long_mode, e_norm, decode, unit)
                    except Exception as exc:
                        logger.exception("_perform_read: fallback read exception")
                        QMessageBox.critical(self, "Read error", str(exc))
                        self.status_label.setText("Read error")
                        return

                    if not rows:
                        QMessageBox.information(self, "No registers", "Read returned no registers.")
                        self.read_model.update_rows([])
                        self.status_label.setText("No registers")
                        return

                    self.read_model.update_rows(rows)
                    self.status_label.setText(f"Read {len(rows)} rows")
                    try:
                        self.status_label.setStyleSheet("")
                        self.log_view.append(f"READ: addr={addr} count={count} long={long_mode} rows={len(rows)}")
                    except Exception:
                        pass
                    return

                # Process registers from controller read
                import struct

                e_norm = e_norm if e_norm != "all" else "big"

                rows: List[ReadRow] = []
                if long_mode and decode:
                    # combine pairs into 32-bit values
                    for i in range(max(1, count)):
                        ri = i * 2
                        if ri + 1 >= len(regs):
                            break
                        r1 = regs[ri]
                        r2 = regs[ri + 1]
                        b1 = int(r1).to_bytes(2, byteorder="big", signed=False)
                        b2 = int(r2).to_bytes(2, byteorder="big", signed=False)
                        bv = b1 + b2

                        if e_norm == "big":
                            raw_be = bv
                        elif e_norm == "little":
                            raw_be = bv[::-1]
                        elif e_norm == "mid-big":
                            raw_be = bytes([bv[2], bv[3], bv[0], bv[1]])
                        else:
                            raw_be = bytes([bv[1], bv[0], bv[3], bv[2]])

                        try:
                            i32 = int.from_bytes(raw_be, byteorder="big", signed=True)
                        except Exception:
                            i32 = 0
                        try:
                            f32 = struct.unpack("!f", raw_be)[0]
                        except Exception:
                            f32 = None

                        hexv = "0x" + bv.hex().upper()
                        rows.append(ReadRow(index=str(addr + ri), hex_value=hexv, int_value=i32, float16=f32))
                else:
                    for i, r in enumerate(regs):
                        idx_disp = str(addr + i)
                        b = int(r).to_bytes(2, byteorder="big", signed=False)
                        hexv = "0x" + b.hex().upper()

                        bb = b[::-1] if e_norm == "little" else b

                        u = int.from_bytes(bb, byteorder="big", signed=False)
                        i16 = u if u < 0x8000 else u - 0x10000
                        try:
                            f16 = from_bytes_to_float16(bb)
                        except Exception:
                            f16 = None
                        rows.append(ReadRow(index=idx_disp, hex_value=hexv, int_value=i16, float16=f16))

                self.read_model.update_rows(rows)
                self.status_label.setText(f"Read {len(rows)} rows")
                try:
                    self.status_label.setStyleSheet("")
                    self.log_view.append(f"READ: addr={addr} count={count} long={long_mode} rows={len(rows)}")
                except Exception:
                    pass
                # clear details when new read results appear
                try:
                    self.read_details.clear()
                except Exception:
                    pass
            finally:
                # re-enable read button
                try:
                    self.btn_read.setEnabled(True)
                except Exception:
                    pass

    async def _perform_write(self) -> None:
        addr = self._parse_address(self.write_addr_edit.text())
        if addr is None:
            QMessageBox.warning(self, "Invalid address", "Enter a valid decimal or 0xHEX address for write.")
            return

        # Require explicit Connect action from the top-bar before allowing writes.
        controller_running = getattr(self, 'controller', None) and getattr(self.controller, 'running', False)
        serial_direct = getattr(self, '_direct_serial_uri', None) is not None
        if not (controller_running or serial_direct):
            QMessageBox.warning(self, "Not connected", "Please use the Connect button in the top bar to establish a connection before writing.")
            return

        long_mode = self.write_long_checkbox.isChecked()
        e_norm = self._normalize_endian(self.write_endian_combo.currentText(), allow_all=False)
        if e_norm is None:
            QMessageBox.warning(self, "Invalid endian", "Select a valid endian option.")
            return

        float_mode = self.write_float_checkbox.isChecked()
        signed = self.write_signed_checkbox.isChecked()
        value_text = self.write_value_edit.text().strip()
        if not value_text:
            QMessageBox.warning(self, "Missing value", "Enter a value to write.")
            return

        # Get unit ID from connection panel
        unit = self._parse_int(self.unit_edit.text()) or 1

        # Build register payload using CLI-style encoding
        try:
            regs = encode_value_to_registers(value_text, long_mode, e_norm, float_mode, signed)
        except ValueError as ve:
            QMessageBox.warning(self, "Validation error", str(ve))
            return

        # Check if we can use the running controller's transport
        if getattr(self, 'controller', None) and getattr(self.controller, 'running', False):
            # Check if transport is actually connected
            if self.controller._use_manager and self.controller._manager:
                if not self.controller._manager._connected_event.is_set():
                    QMessageBox.warning(self, "Not connected", "Controller is starting; wait for connection to complete.")
                    return
            # Use CoreController Modbus methods (shared transport)
            try:
                success = await self.controller.modbus_write_registers(unit, addr, regs)
                if success:
                    self.write_status_label.setText("Write OK")
                else:
                    self.write_status_label.setText("Write failed")
            except Exception as exc:
                QMessageBox.critical(self, "Write error", str(exc))
            return
        else:
            # Fall back to standalone pymodbus client
            uri = self.build_uri()
            # Basic validation similar to read
            parsed = urlparse(uri)
            scheme = parsed.scheme or 'serial'
            if scheme == 'serial':
                port = parsed.netloc or parsed.path.lstrip('/')
                if ':' in port:
                    port = port.split(':', 1)[0]
                if not port:
                    QMessageBox.warning(self, "Missing port", "Select or enter a serial port before writing.")
                    return
            else:
                host = parsed.hostname
                if not host:
                    QMessageBox.warning(self, "Missing host", "Enter a TCP host before writing.")
                    return

            try:
                ok = await self._write_registers(uri, addr, unit, regs)
            except Exception as exc:
                logger.exception("_perform_write: fallback write exception")
                QMessageBox.critical(self, "Write error", str(exc))
                return

            self.write_status_label.setText("Write OK" if ok else "Write failed")
            try:
                self.log_view.append(f"WRITE: addr={addr} unit={unit} values={regs} ok={ok}")
            except Exception:
                pass

    # --- Monitor tab slots ---

    def on_monitor_decode_changed(self, decoding: str):
        """Handle decoding scheme change - update table display."""
        self.monitor_model.set_decoding(decoding)
        # Refresh details panel if a row is selected
        try:
            sel = self.monitor_table.selectionModel().selectedRows()
            if sel:
                self.on_monitor_selection_changed()
        except Exception:
            pass

    def _format_register_details(self, regs: List[int], start_addr: int, endian: str) -> str:
        """Format detailed decoding for a list of 16-bit registers."""
        import struct

        lines = []
        lines.append(f"Start addr: {start_addr}")
        lines.append(f"Endian: {endian}")
        lines.append("")
        for i, r in enumerate(regs):
            addr = start_addr + i
            hexv = f"0x{r:04X}"
            unsigned = r
            signed = r if r < 0x8000 else r - 0x10000
            try:
                b = r.to_bytes(2, byteorder="big", signed=False)
                bb = b[::-1] if endian == "little" else b
                f16 = from_bytes_to_float16(bb)
                f16s = f"{f16:.6g}" if f16 is not None else "—"
            except Exception:
                f16s = "—"
            lines.append(f"[{i}] addr={addr}  hex={hexv}  u={unsigned}  s={signed}  f16={f16s}")

        # show combined 32-bit interpretations for adjacent pairs
        if len(regs) >= 2:
            lines.append("")
            lines.append("32-bit pairs:")
            for i in range(0, len(regs) - 1, 2):
                a = regs[i]
                b = regs[i + 1]
                bv = a.to_bytes(2, byteorder='big') + b.to_bytes(2, byteorder='big')
                if endian == 'big':
                    raw_be = bv
                elif endian == 'little':
                    raw_be = bv[::-1]
                elif endian == 'mid-big':
                    raw_be = bytes([bv[2], bv[3], bv[0], bv[1]])
                else:
                    raw_be = bytes([bv[1], bv[0], bv[3], bv[2]])
                try:
                    i32 = int.from_bytes(raw_be, byteorder='big', signed=True)
                except Exception:
                    i32 = 0
                try:
                    f32 = struct.unpack('!f', raw_be)[0]
                    f32s = f"{f32:.6g}"
                except Exception:
                    f32s = '—'
                lines.append(f"pair[{i}//{i+1}] i32={i32}  f32={f32s}  raw=0x{raw_be.hex().upper()}")

        return "\n".join(lines)

    async def _read_registers(self, uri: str, addr: int, count: int, long_mode: bool, endian: str, decode: bool, unit: int) -> Optional[List[ReadRow]]:
        """Unified read helper for the GUI.

        Prefers `CoreController.modbus_read_holding_registers` when the controller
        is running (which already manages locking). Falls back to the blocking
        `run_gui_read` executed in a thread when no controller is available.
        """
        # If controller is running, prefer its read helper which is async and
        # acquires transport locks correctly.
        if getattr(self, 'controller', None) and getattr(self.controller, 'running', False):
            regs = await self.controller.modbus_read_holding_registers(unit, addr, count * (2 if long_mode else 1))
            if regs is None:
                return None
            # Convert to ReadRow list similar to run_gui_read when decode requested
            if long_mode and decode:
                rows: List[ReadRow] = []
                import struct
                for i in range(max(1, count)):
                    ri = i * 2
                    if ri + 1 >= len(regs):
                        break
                    r1 = regs[ri]
                    r2 = regs[ri + 1]
                    b1 = int(r1).to_bytes(2, byteorder="big", signed=False)
                    b2 = int(r2).to_bytes(2, byteorder="big", signed=False)
                    bv = b1 + b2
                    # respect endian argument (normalize 'all' to big)
                    e_norm = endian if endian != "all" else "big"
                    if e_norm == "big":
                        raw_be = bv
                    elif e_norm == "little":
                        raw_be = bv[::-1]
                    elif e_norm == "mid-big":
                        raw_be = bytes([bv[2], bv[3], bv[0], bv[1]])
                    else:
                        raw_be = bytes([bv[1], bv[0], bv[3], bv[2]])
                    try:
                        i32 = int.from_bytes(raw_be, byteorder="big", signed=True)
                    except Exception:
                        i32 = 0
                    try:
                        f32 = struct.unpack("!f", raw_be)[0]
                    except Exception:
                        f32 = None
                    hexv = "0x" + bv.hex().upper()
                    rows.append(ReadRow(index=str(addr + ri), hex_value=hexv, int_value=i32, float16=f32))
                return rows
            else:
                rows: List[ReadRow] = []
                for i, r in enumerate(regs):
                    idx_disp = str(addr + i)
                    b = int(r).to_bytes(2, byteorder="big", signed=False)
                    hexv = "0x" + b.hex().upper()
                    e_norm = endian if endian != "all" else "big"
                    bb = b[::-1] if e_norm == "little" else b
                    u = int.from_bytes(bb, byteorder="big", signed=False)
                    i16 = u if u < 0x8000 else u - 0x10000
                    try:
                        f16 = from_bytes_to_float16(bb)
                    except Exception:
                        f16 = None
                    rows.append(ReadRow(index=idx_disp, hex_value=hexv, int_value=i16, float16=f16))
                return rows

        # Fallback: run blocking pymodbus read in a thread
        try:
            return await asyncio.to_thread(run_gui_read, uri, addr, count, long_mode, endian, decode)
        except Exception:
            logger.exception("_read_registers: fallback run_gui_read failed")
            return None

    async def _write_registers(self, uri: str, addr: int, unit: int, values: List[int]) -> bool:
        """Unified write helper for GUI: prefer controller, fall back to blocking write."""
        if getattr(self, 'controller', None) and getattr(self.controller, 'running', False):
            try:
                return await self.controller.modbus_write_registers(unit, addr, values)
            except Exception:
                logger.exception("_write_registers: controller write failed")
                return False

        try:
            ok, _ = await asyncio.to_thread(run_gui_write, uri, addr, len(values) > 1, 'big', False, False, str(values[0] if values else 0))
            return ok
        except Exception:
            logger.exception("_write_registers: fallback run_gui_write failed")
            return False

    def _compute_decoding_rows(self, regs: List[int]) -> List[dict]:
        """Return a list of decoding dicts for each endianness.

        Each dict contains keys: Format, Hex(s), UInt16, Int16, Float16, Hex32, Int32, Float32
        """
        import struct

        def decode_16(r, byteorder):
            b = r.to_bytes(2, byteorder='big')
            bb = b if byteorder == 'big' else b[::-1]
            hexs = bb.hex().upper()
            u = int.from_bytes(bb, byteorder='big', signed=False)
            s = u if u < 0x8000 else u - 0x10000
            try:
                f16 = from_bytes_to_float16(bb)
            except Exception:
                f16 = None
            return hexs, u, s, f16

        def decode_32(a, b, order):
            bv = a.to_bytes(2, byteorder='big') + b.to_bytes(2, byteorder='big')
            if order == 'big':
                raw = bv
            elif order == 'little':
                raw = bv[::-1]
            elif order == 'mid-big':
                raw = bytes([bv[2], bv[3], bv[0], bv[1]])
            else:
                raw = bytes([bv[1], bv[0], bv[3], bv[2]])
            hex32 = raw.hex().upper()
            try:
                i32 = int.from_bytes(raw, byteorder='big', signed=True)
            except Exception:
                i32 = None
            try:
                u32 = int.from_bytes(raw, byteorder='big', signed=False)
            except Exception:
                u32 = None
            try:
                f32 = struct.unpack('!f', raw)[0]
            except Exception:
                f32 = None
            return hex32, u32, i32, f32

        rows = []
        # For single 16-bit registers only show Big/Little. For 32-bit (pairs) show all four permutations.
        if len(regs) >= 2:
            orders = [('Big', 'big'), ('Little', 'little'), ('Mid-Big', 'mid-big'), ('Mid-Little', 'mid-little')]
        else:
            orders = [('Big', 'big'), ('Little', 'little')]
        for label, order in orders:
            if not regs:
                rows.append({
                    'Format': label,
                    'Hex': '',
                    'UInt16': '',
                    'Int16': '',
                    'Float16': '',
                    'Hex32': '',
                    'UInt32': '',
                    'Int32': '',
                    'Float32': '',
                })
                continue

            # For simplicity show first register details and optional first pair 32-bit
            first = regs[0]
            hexs, u, s, f16 = decode_16(first, order)
            hex_s_display = hexs
            hex32_str = ''
            u32_str = ''
            i32_str = ''
            f32_str = ''
            if len(regs) >= 2:
                hex32, u32_val, i32_val, f32_val = decode_32(regs[0], regs[1], order)
                hex32_str = hex32 or ''
                u32_str = '' if u32_val is None else str(u32_val)
                i32_str = '' if i32_val is None else str(i32_val)
                if f32_val is None:
                    f32_str = ''
                else:
                    f32_str = f"{f32_val:.6g}" if isinstance(f32_val, (int, float)) else str(f32_val)

            rows.append({
                'Format': label,
                'Hex': hex_s_display,
                'UInt16': str(u),
                'Int16': str(s),
                'Float16': '' if f16 is None else (f"{f16:.6g}" if isinstance(f16, (int, float)) else str(f16)),
                'Hex32': hex32_str,
                'UInt32': u32_str,
                'Int32': i32_str,
                'Float32': f32_str,
            })

        return rows

    def on_read_selection_changed(self, selected=None, deselected=None):
        try:
            sel = self.read_table.selectionModel().selectedRows()
            if not sel:
                return
            idx = sel[0].row()
            row = self.read_model._rows[idx]
            # Build decoding table for this read; support 16-bit and 32-bit results
            # Try to parse the stored hex_value to recover raw 16-bit register(s)
            regs: List[int] = []
            try:
                hexstr = row.hex_value if isinstance(row.hex_value, str) else None
                if hexstr and hexstr.startswith('0x'):
                    hb = bytes.fromhex(hexstr[2:])
                    if len(hb) == 2:
                        regs = [int.from_bytes(hb, byteorder='big')]
                    elif len(hb) == 4:
                        regs = [int.from_bytes(hb[0:2], byteorder='big'), int.from_bytes(hb[2:4], byteorder='big')]
                    else:
                        regs = [row.int_value & 0xFFFF]
                else:
                    regs = [row.int_value & 0xFFFF]
            except Exception:
                regs = [row.int_value & 0xFFFF]

            try:
                decoding_rows = self._compute_decoding_rows(regs)
            except Exception:
                # If decoding failed, fall back to Big/Little basic numeric view
                logger.exception("Decoding helper failed for regs=%s", regs)
                decoding_rows = []
                for label, order in [('Big', 'big'), ('Little', 'little')]:
                    try:
                        b = regs[0].to_bytes(2, byteorder='big')
                        bb = b if order == 'big' else b[::-1]
                        hexs = bb.hex().upper()
                        u = int.from_bytes(bb, byteorder='big', signed=False)
                        s = u if u < 0x8000 else u - 0x10000
                        try:
                            f16 = from_bytes_to_float16(bb)
                            f16s = '' if f16 is None else f"{f16:.6g}"
                        except Exception:
                            f16s = ''
                    except Exception:
                        hexs = ''
                        u = ''
                        s = ''
                        f16s = ''
                    decoding_rows.append({'Format': label, 'Hex': hexs, 'UInt16': str(u), 'Int16': str(s), 'Float16': f16s})
            # Populate read_details_table
            table = self.read_details_table
            # Show only the appropriate set of columns depending on register width.
            # For 32-bit longs we hide the 16-bit columns (1-4) and show 32-bit columns (5-8).
            is_32 = len(regs) >= 2
            if is_32:
                # hide 16-bit columns
                table.setColumnHidden(1, True)
                table.setColumnHidden(2, True)
                table.setColumnHidden(3, True)
                table.setColumnHidden(4, True)
                # show 32-bit columns
                table.setColumnHidden(5, False)
                table.setColumnHidden(6, False)
                table.setColumnHidden(7, False)
                table.setColumnHidden(8, False)
            else:
                # show 16-bit columns
                table.setColumnHidden(1, False)
                table.setColumnHidden(2, False)
                table.setColumnHidden(3, False)
                table.setColumnHidden(4, False)
                # hide 32-bit columns
                table.setColumnHidden(5, True)
                table.setColumnHidden(6, True)
                table.setColumnHidden(7, True)
                table.setColumnHidden(8, True)
            table.setRowCount(len(decoding_rows))
            for r_idx, d in enumerate(decoding_rows):
                table.setItem(r_idx, 0, QTableWidgetItem(d['Format']))
                table.setItem(r_idx, 1, QTableWidgetItem(d.get('Hex', '')))
                table.setItem(r_idx, 2, QTableWidgetItem(d.get('UInt16', '')))
                table.setItem(r_idx, 3, QTableWidgetItem(d.get('Int16', '')))
                table.setItem(r_idx, 4, QTableWidgetItem(d.get('Float16', '')))
                # 32-bit columns (may be empty for single-register reads)
                table.setItem(r_idx, 5, QTableWidgetItem(d.get('Hex32', '')))
                table.setItem(r_idx, 6, QTableWidgetItem(d.get('UInt32', '')))
                table.setItem(r_idx, 7, QTableWidgetItem(d.get('Int32', '')))
                table.setItem(r_idx, 8, QTableWidgetItem(d.get('Float32', '')))
        except Exception:
            pass

    def on_monitor_selection_changed(self, selected=None, deselected=None):
        try:
            sel = self.monitor_table.selectionModel().selectedRows()
            if not sel:
                return
            idx = sel[0].row()
            sample = list(self.monitor_model._samples)[idx]
            # format details table from raw registers across endianness
            rows = self._compute_decoding_rows(sample.raw_registers)
            table = self.monitor_details_table
            is_32 = len(sample.raw_registers) >= 2
            if is_32:
                table.setColumnHidden(1, True)
                table.setColumnHidden(2, True)
                table.setColumnHidden(3, True)
                table.setColumnHidden(4, True)
                table.setColumnHidden(5, False)
                table.setColumnHidden(6, False)
                table.setColumnHidden(7, False)
                table.setColumnHidden(8, False)
            else:
                table.setColumnHidden(1, False)
                table.setColumnHidden(2, False)
                table.setColumnHidden(3, False)
                table.setColumnHidden(4, False)
                table.setColumnHidden(5, True)
                table.setColumnHidden(6, True)
                table.setColumnHidden(7, True)
                table.setColumnHidden(8, True)
            table.setRowCount(len(rows))
            for r_idx, d in enumerate(rows):
                table.setItem(r_idx, 0, QTableWidgetItem(d['Format']))
                table.setItem(r_idx, 1, QTableWidgetItem(d.get('Hex', '')))
                table.setItem(r_idx, 2, QTableWidgetItem(d.get('UInt16', '')))
                table.setItem(r_idx, 3, QTableWidgetItem(d.get('Int16', '')))
                table.setItem(r_idx, 4, QTableWidgetItem(d.get('Float16', '')))
                table.setItem(r_idx, 5, QTableWidgetItem(d.get('Hex32', '')))
                table.setItem(r_idx, 6, QTableWidgetItem(d.get('UInt32', '')))
                table.setItem(r_idx, 7, QTableWidgetItem(d.get('Int32', '')))
                table.setItem(r_idx, 8, QTableWidgetItem(d.get('Float32', '')))
            if sample.error:
                # Prepend an error row if present
                table.insertRow(0)
                table.setItem(0, 0, QTableWidgetItem("ERROR"))
                table.setItem(0, 1, QTableWidgetItem(sample.error))
        except Exception:
            pass

    @qasync.asyncSlot()
    async def on_monitor_start_clicked(self):
        """Start the monitor polling task."""
        # Require connection first
        controller_running = getattr(self, 'controller', None) and getattr(self.controller, 'running', False)
        serial_direct = getattr(self, '_direct_serial_uri', None) is not None
        if not (controller_running or serial_direct):
            QMessageBox.warning(self, "Not connected", "Please use the Connect button in the top bar to establish a connection before monitoring.")
            return

        # Prevent starting if already running
        if self._monitor_task and not self._monitor_task.done():
            QMessageBox.information(self, "Monitor running", "Monitor is already running. Stop it first.")
            return

        # Parse and validate monitor configuration
        addr = self._parse_address(self.monitor_addr_edit.text())
        if addr is None:
            QMessageBox.warning(self, "Invalid address", "Enter a valid decimal or 0xHEX address for monitoring.")
            return

        count = self._parse_int(self.monitor_count_edit.text()) or 1
        long_mode = self.monitor_long_checkbox.isChecked()
        e_norm = self._normalize_endian(self.monitor_endian_combo.currentText(), allow_all=False)
        if e_norm is None:
            QMessageBox.warning(self, "Invalid endian", "Select a valid endian option.")
            return

        interval_ms = self.monitor_interval_spin.value()
        interval_sec = interval_ms / 1000.0

        unit = self._parse_int(self.unit_edit.text()) or 1
        regs_to_read = count * 2 if long_mode else count

        # Configure the model with register count for column headers
        self.monitor_model.set_config(regs_to_read, long_mode, e_norm)

        # Update UI state
        self.btn_monitor_start.setEnabled(False)
        self.btn_monitor_stop.setEnabled(True)
        self.monitor_status_label.setText(f"Monitoring address {addr} every {interval_ms}ms...")

        # Start the polling task
        self._monitor_task = asyncio.create_task(
            self._monitor_polling_loop(addr, count, regs_to_read, long_mode, e_norm, unit, interval_sec)
        )

    @qasync.asyncSlot()
    async def on_monitor_stop_clicked(self):
        """Stop the monitor polling task."""
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            self._monitor_task = None

        # Update UI state
        self.btn_monitor_start.setEnabled(True)
        self.btn_monitor_stop.setEnabled(False)
        self.monitor_status_label.setText("Stopped")

    @qasync.asyncSlot()
    async def on_monitor_clear_clicked(self):
        """Clear all monitor samples from the table."""
        self.monitor_model.clear_samples()
        self.monitor_status_label.setText("Cleared")

    async def _monitor_polling_loop(self, addr: int, count: int, regs_to_read: int, long_mode: bool, endian: str, unit: int, interval_sec: float):
        """Async polling loop that reads from the device at configured interval."""
        poll_count = 0
        while True:
            try:
                poll_count += 1
                timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]

                # Attempt to read registers
                try:
                    # Check if we can use the running controller's transport
                    if getattr(self, 'controller', None) and getattr(self.controller, 'running', False):
                        # Use CoreController Modbus methods
                        if self.controller._use_manager and self.controller._manager:
                            if not self.controller._manager._connected_event.is_set():
                                raise RuntimeError("Controller not connected")
                        regs = await self.controller.modbus_read_holding_registers(unit, addr, regs_to_read)
                        if regs is None:
                            raise RuntimeError("Read returned None")
                    else:
                        # Fall back to standalone pymodbus client
                        uri = self.build_uri()
                        regs_data = await self._read_registers(uri, addr, count, long_mode, endian, False, unit)
                        # Extract raw register values from ReadRow objects
                        regs = [r.int_value & 0xFFFF for r in regs_data] if regs_data else []

                    # Create one sample with all raw register values for this interval
                    sample = MonitorSample(
                        timestamp=timestamp,
                        raw_registers=list(regs),
                        address_start=addr,
                        unit_id=unit,
                    )
                    self.monitor_model.add_sample(sample)

                    # Auto-scroll to bottom (newest entry)
                    try:
                        self.monitor_table.scrollToBottom()
                    except Exception:
                        pass

                    # Update status
                    self.monitor_status_label.setText(f"Monitoring (poll #{poll_count})")
                    # clear monitor details for new sample selection
                    try:
                        self.monitor_details.clear()
                    except Exception:
                        pass

                except Exception as exc:
                    # Record error as a sample row
                    error_sample = MonitorSample(
                        timestamp=timestamp,
                        raw_registers=[],
                        address_start=addr,
                        unit_id=unit,
                        error=str(exc),
                    )
                    self.monitor_model.add_sample(error_sample)
                    logger.exception("Monitor poll error")

                # Wait for next poll interval
                await asyncio.sleep(interval_sec)

            except asyncio.CancelledError:
                # Task cancelled, exit loop cleanly
                raise
            except Exception as exc:
                logger.exception("Unexpected error in monitor polling loop")
                # Continue polling despite errors
                await asyncio.sleep(interval_sec)

def main():
    app = QApplication(sys.argv)
    # set application icon early so it becomes the taskbar icon on Windows
    if os.path.exists(ICON_PATH):
        try:
            app.setWindowIcon(QIcon(ICON_PATH))
        except Exception:
            pass

    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    window = MainWindow()
    if os.path.exists(ICON_PATH):
        try:
            window.setWindowIcon(QIcon(ICON_PATH))
        except Exception:
            pass
    window.show()

    with loop:
        loop.run_forever()

if __name__ == "__main__":
    main()
