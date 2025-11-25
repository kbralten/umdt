import sys
import os
import asyncio
from dataclasses import dataclass
from typing import List, Optional, Union
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
from umdt.core.data_types import (
    DATA_TYPE_PROPERTIES,
    DataType,
    is_bit_type,
    is_register_type,
)
from umdt.core.prober import Prober, TargetSpec
from serial.tools import list_ports
from urllib.parse import urlparse, parse_qs
from umdt.utils.ieee754 import from_bytes_to_float16
from umdt.utils.modbus_compat import (
    call_read_method,
    call_write_method,
    create_client,
    close_client,
    read_holding_registers,
    read_input_registers,
    read_coils,
    read_discrete_inputs,
    write_registers,
    write_register,
    write_coil,
    write_coils,
    invoke_method,
)
from umdt.utils.decoding import decode_registers, decode_to_table_dict
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
    int_value: Optional[int]
    float16: Optional[float]
    data_type: DataType
    bool_value: Optional[bool] = None


@dataclass
class MonitorSample:
    """A single monitor poll sample (one interval)."""
    timestamp: str
    raw_registers: List[Union[int, bool]]
    address_start: int
    unit_id: int
    data_type: DataType
    error: Optional[str] = None


class MonitorModel(QAbstractTableModel):
    """Table model for monitor samples with one row per poll interval."""
    
    def __init__(self, max_samples: int = 1000):
        super().__init__()
        self._samples: deque = deque(maxlen=max_samples)
        self._max_samples = max_samples
        self._value_count = 1
        self._decoding = "Signed"  # Hex, Unsigned, Signed, Float16
        self._long_mode = False
        self._endian = "big"

        self._data_type: DataType = DataType.HOLDING

    def set_config(self, value_count: int, long_mode: bool, endian: str, data_type: DataType):
        """Configure column count and decoding parameters."""
        self._value_count = value_count
        self._long_mode = long_mode
        self._endian = endian
        self._data_type = data_type
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
        return 1 + self._value_count

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

            # Decode value based on datatype/decoding mode
            if is_bit_type(sample.data_type):
                bit_val = bool(sample.raw_registers[reg_idx])
                return "ON" if bit_val else "OFF"

            reg_val = int(sample.raw_registers[reg_idx])

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


def run_gui_read(
    uri: str,
    address: int,
    value_count: int,
    long_mode: bool,
    endian: str,
    decode: bool,
    data_type: DataType,
    unit: int,
) -> List[ReadRow]:
    """Blocking worker to perform a Modbus read for the GUI.
    
    Raises RuntimeError with user-friendly messages on errors.
    """

    from urllib.parse import urlparse, parse_qs

    parsed = urlparse(uri)
    scheme = parsed.scheme or "serial"
    qs = parse_qs(parsed.query or "")

    # Parse connection details
    client = None
    try:
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
            try:
                client = create_client(kind="serial", serial_port=port, baudrate=baud)
            except Exception as e:
                raise RuntimeError(f"Failed to create serial client for {port}: {e}")
        else:
            host = parsed.hostname or "127.0.0.1"
            tcp_port = parsed.port or int(qs.get("port", ["502"])[0])
            try:
                client = create_client(kind="tcp", host=host, port=tcp_port)
            except Exception as e:
                raise RuntimeError(f"Failed to create TCP client for {host}:{tcp_port}: {e}")

        # Connect to device
        try:
            connected = client.connect()
        except Exception as e:
            raise RuntimeError(f"Connection error: {e}")
        
        if not connected:
            if scheme == "serial":
                raise RuntimeError(f"Failed to connect to serial port {port} at {baud} baud. Check port name and permissions.")
            else:
                raise RuntimeError(f"Failed to connect to {host}:{tcp_port}. Check host/port and network connectivity.")
    except Exception:
        # Ensure any partially-created client is closed
        try:
            close_client(client)
        except Exception:
            pass
        raise

    props = DATA_TYPE_PROPERTIES[data_type]
    if not props.readable or not props.pymodbus_read_method:
        client.close()
        raise RuntimeError(f"Data type {data_type.value} cannot be read")

    total_count = value_count
    if is_register_type(data_type):
        total_count = max(1, value_count) * (2 if long_mode else 1)
    else:
        total_count = max(1, value_count)

    # Perform read using compat wrappers
    try:
        _read_map = {
            'read_holding_registers': read_holding_registers,
            'read_input_registers': read_input_registers,
            'read_coils': read_coils,
            'read_discrete_inputs': read_discrete_inputs,
        }
        reader = _read_map.get(props.pymodbus_read_method)
        if reader:
            response = reader(client, address, total_count, unit)
        else:
            # Fall back to compatibility helper invocation if mapping missing
            response = invoke_method(client, props.pymodbus_read_method, address, total_count, unit)
    except Exception as e:
        try:
            close_client(client)
        except Exception:
            pass
        raise RuntimeError(f"Modbus read error: {e}")
    finally:
        try:
            close_client(client)
        except Exception:
            pass

    # Check response for protocol errors
    if hasattr(response, 'isError') and response.isError():
        # Try to extract exception code for better error messages
        error_msg = "Modbus protocol error"
        if hasattr(response, 'exception_code'):
            code = response.exception_code
            error_codes = {
                1: "Illegal function",
                2: "Illegal data address",
                3: "Illegal data value",
                4: "Slave device failure",
                5: "Acknowledge (request accepted, processing)",
                6: "Slave device busy",
                8: "Memory parity error",
                10: "Gateway path unavailable",
                11: "Gateway target device failed to respond",
            }
            error_msg = f"Modbus exception {code}: {error_codes.get(code, 'Unknown error')}"
        raise RuntimeError(error_msg)

    # Extract values from response
    values = None
    attr = 'bits' if is_bit_type(data_type) else 'registers'
    values = getattr(response, attr, None)
    if values is None:
        try:
            values = list(response)
        except Exception:
            values = []
    
    if not values:
        raise RuntimeError(f"Read returned no data. Check address {address} and unit ID {unit}.")

    return _build_rows_from_values(values, data_type, address, value_count, long_mode, endian, decode)


def _build_rows_from_values(
    values: List[Union[int, bool]],
    data_type: DataType,
    address: int,
    value_count: int,
    long_mode: bool,
    endian: str,
    decode: bool,
) -> List[ReadRow]:
    rows: List[ReadRow] = []
    e_norm = endian if endian != "all" else "big"

    if is_bit_type(data_type):
        for i, bit in enumerate(values[:value_count]):
            idx = str(address + i)
            state = bool(bit)
            rows.append(
                ReadRow(
                    index=idx,
                    hex_value="—",
                    int_value=1 if state else 0,
                    float16=None,
                    data_type=data_type,
                    bool_value=state,
                )
            )
        return rows

    regs = [int(v) & 0xFFFF for v in values]
    import struct

    if long_mode and decode:
        for i in range(max(1, value_count)):
            ri = i * 2
            if ri + 1 >= len(regs):
                break
            b1 = regs[ri].to_bytes(2, byteorder="big")
            b2 = regs[ri + 1].to_bytes(2, byteorder="big")
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
            rows.append(
                ReadRow(
                    index=str(address + ri),
                    hex_value=hexv,
                    int_value=i32,
                    float16=f32,
                    data_type=data_type,
                )
            )
        return rows

    for i, r in enumerate(regs[: value_count * (2 if long_mode else 1)]):
        idx = str(address + i)
        b = r.to_bytes(2, byteorder="big", signed=False)
        hexv = "0x" + b.hex().upper()
        bb = b[::-1] if e_norm == "little" else b
        u = int.from_bytes(bb, byteorder="big", signed=False)
        i16 = u if u < 0x8000 else u - 0x10000
        try:
            f16 = from_bytes_to_float16(bb)
        except Exception:
            f16 = None
        rows.append(
            ReadRow(
                index=idx,
                hex_value=hexv,
                int_value=i16,
                float16=f16,
                data_type=data_type,
            )
        )
    return rows


def run_gui_write(
    uri: str,
    address: int,
    long_mode: bool,
    endian: str,
    float_mode: bool,
    signed: bool,
    value_text: str,
    data_type: DataType,
    unit: int,
) -> tuple[bool, str]:
    """Blocking worker that performs a simple Modbus write for the GUI.

    This mirrors a subset of the CLI write validation and encoding logic.
    """
    import math
    import struct
    from urllib.parse import urlparse, parse_qs

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

    # Validate data type is writable
    props = DATA_TYPE_PROPERTIES[data_type]
    if not props.writable or not props.pymodbus_write_method:
        raise RuntimeError(f"Data type {data_type.value} cannot be written")

    parsed = urlparse(uri)
    scheme = parsed.scheme or "serial"
    qs = parse_qs(parsed.query or "")

    client = None
    try:
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
            try:
                client = create_client(kind="serial", serial_port=port, baudrate=baud)
            except Exception as e:
                raise RuntimeError(f"Failed to create serial client for {port}: {e}")
        else:
            host = parsed.hostname or "127.0.0.1"
            tcp_port = parsed.port or int(qs.get("port", ["502"])[0])
            try:
                client = create_client(kind="tcp", host=host, port=tcp_port)
            except Exception as e:
                raise RuntimeError(f"Failed to create TCP client for {host}:{tcp_port}: {e}")

        # Connect
        try:
            connected = client.connect()
        except Exception as e:
            raise RuntimeError(f"Connection error: {e}")
        
        if not connected:
            if scheme == "serial":
                raise RuntimeError(f"Failed to connect to serial port {port} at {baud} baud. Check port name and permissions.")
            else:
                raise RuntimeError(f"Failed to connect to {host}:{tcp_port}. Check host/port and network connectivity.")
    except Exception:
        try:
            close_client(client)
        except Exception:
            pass
        raise

    # Perform write using compat wrappers
    try:
        # For coils, convert register values to boolean list
        if data_type == DataType.COIL:
            # Convert register values to boolean list
            bit_values = []
            for reg in regs:
                # Extract bits from register
                for bit_pos in range(16):
                    if len(bit_values) >= (1 if not long_mode else 32):
                        break
                    bit_values.append(bool((reg >> bit_pos) & 1))
            # If single bit, use write_coil, else write_coils
            if len(bit_values) == 1:
                res = write_coil(client, address, bit_values[0], unit)
            else:
                res = write_coils(client, address, bit_values, unit)
        else:
            # For registers, use appropriate write method
            if long_mode or (float_mode and len(regs) == 2) or len(regs) > 1:
                res = write_registers(client, address, regs, unit)
            else:
                # Single register write
                if props.pymodbus_write_method == 'write_registers':
                    res = write_register(client, address, regs[0], unit)
                else:
                    # Try mapping common names, otherwise call attribute directly
                    if props.pymodbus_write_method == 'write_register':
                        res = write_register(client, address, regs[0], unit)
                    else:
                        # Use compatibility helper for non-standard write method names
                        res = invoke_method(client, props.pymodbus_write_method, address, regs, unit)
        
        # Check for protocol errors
        if hasattr(res, "isError") and res.isError():
            error_msg = "Modbus protocol error"
            if hasattr(res, 'exception_code'):
                code = res.exception_code
                error_codes = {
                    1: "Illegal function",
                    2: "Illegal data address",
                    3: "Illegal data value",
                    4: "Slave device failure",
                    5: "Acknowledge (request accepted, processing)",
                    6: "Slave device busy",
                    8: "Memory parity error",
                    10: "Gateway path unavailable",
                    11: "Gateway target device failed to respond",
                }
                error_msg = f"Modbus exception {code}: {error_codes.get(code, 'Unknown error')}"
            raise RuntimeError(error_msg)
        return True, "Write OK"
    except RuntimeError:
        raise
    except Exception as e:
        try:
            close_client(client)
        except Exception:
            pass
        raise RuntimeError(f"Modbus write error: {e}")
    finally:
        try:
            close_client(client)
        except Exception:
            pass

# project icon (placed next to main scripts or bundled by PyInstaller into _MEIPASS)
_RESOURCE_BASE = getattr(sys, '_MEIPASS', os.path.dirname(__file__))
ICON_PATH = os.path.join(_RESOURCE_BASE, "umdt.ico")

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

        self.datatype_combo = QComboBox()
        for dtype in (DataType.HOLDING, DataType.INPUT, DataType.COIL, DataType.DISCRETE):
            props = DATA_TYPE_PROPERTIES[dtype]
            self.datatype_combo.addItem(props.label)
        # Store mapping separately since QComboBox userData can be unreliable
        self._datatype_map = {
            0: DataType.HOLDING,
            1: DataType.INPUT,
            2: DataType.COIL,
            3: DataType.DISCRETE,
        }
        conn_row.addWidget(QLabel("Type:"))
        conn_row.addWidget(self.datatype_combo)

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
        self.scan_tab = QWidget()
        self.tabs.addTab(self.interact_tab, "Interact")
        self.tabs.addTab(self.monitor_tab, "Monitor")
        self.tabs.addTab(self.scan_tab, "Scan")
        self.probe_tab = QWidget()
        self.tabs.addTab(self.probe_tab, "Probe")

        # Build Interact tab layout (Read / Write panels)
        self._build_interact_tab()

        # Build Monitor tab layout
        self._build_monitor_tab()

        # Build Scan tab layout
        self._build_scan_tab()
        # Build Probe tab layout
        self._build_probe_tab()

        root_layout.addWidget(self.tabs)

        # Lightweight log view below tabs
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumHeight(160)
        root_layout.addWidget(self.log_view)

        # Async lock to prevent concurrent read operations
        self._read_lock = asyncio.Lock()
        # Monitor polling task
        self._monitor_task: Optional[asyncio.Task] = None
        # Scan task
        self._scan_task: Optional[asyncio.Task] = None
        # Store connection state: None = disconnected, str = URI
        self._connection_uri: Optional[str] = None

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

    def _current_data_type(self) -> DataType:
        idx = self.datatype_combo.currentIndex()
        return self._datatype_map.get(idx, DataType.HOLDING)

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

    def _build_scan_tab(self) -> None:
        """Build the Scan tab UI with address range scanning."""
        layout = QVBoxLayout()

        # --- Scan configuration panel ---
        config_row = QHBoxLayout()

        self.scan_start_edit = QLineEdit()
        self.scan_start_edit.setPlaceholderText("Start (e.g. 0 or 0x0000)")
        self.scan_start_edit.setText("0")
        self.scan_start_edit.setMaximumWidth(150)

        self.scan_end_edit = QLineEdit()
        self.scan_end_edit.setPlaceholderText("End (e.g. 100 or 0x0064)")
        self.scan_end_edit.setText("100")
        self.scan_end_edit.setMaximumWidth(150)

        config_row.addWidget(QLabel("Start Address:"))
        config_row.addWidget(self.scan_start_edit)
        config_row.addWidget(QLabel("End Address:"))
        config_row.addWidget(self.scan_end_edit)

        self.btn_scan_start = QPushButton("Start Scan")
        self.btn_scan_stop = QPushButton("Stop")
        self.btn_scan_stop.setEnabled(False)
        self.btn_scan_clear = QPushButton("Clear Results")

        config_row.addWidget(self.btn_scan_start)
        config_row.addWidget(self.btn_scan_stop)
        config_row.addWidget(self.btn_scan_clear)
        config_row.addStretch()

        layout.addLayout(config_row)

        # --- Scan status ---
        self.scan_status_label = QLabel("Ready to scan")
        layout.addWidget(self.scan_status_label)

        # --- Scan results table ---
        self.scan_table = QTableWidget()
        self.scan_table.setColumnCount(3)
        self.scan_table.setHorizontalHeaderLabels(["Address (Dec)", "Address (Hex)", "Status"])
        header = self.scan_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        self.scan_table.verticalHeader().setVisible(False)
        layout.addWidget(self.scan_table)

        self.scan_tab.setLayout(layout)

        # Wire buttons
        self.btn_scan_start.clicked.connect(self.on_scan_start_clicked)
        self.btn_scan_stop.clicked.connect(self.on_scan_stop_clicked)
        self.btn_scan_clear.clicked.connect(self.on_scan_clear_clicked)


    def _build_probe_tab(self) -> None:
        """Build the Probe tab UI and wire controls."""
        layout = QVBoxLayout()

        # --- Parameter inputs row ---
        params_row = QHBoxLayout()
        self.probe_hosts_edit = QLineEdit()
        self.probe_hosts_edit.setPlaceholderText("Hosts (CSV or ranges)")
        self.probe_ports_edit = QLineEdit()
        self.probe_ports_edit.setPlaceholderText("Ports (CSV)")
        self.probe_serials_edit = QLineEdit()
        self.probe_serials_edit.setPlaceholderText("Serial ports (CSV)")
        self.probe_bauds_edit = QLineEdit()
        self.probe_bauds_edit.setPlaceholderText("Bauds (CSV)")
        self.probe_units_edit = QLineEdit("1")
        self.probe_units_edit.setMaximumWidth(120)

        params_row.addWidget(QLabel("Hosts:"))
        params_row.addWidget(self.probe_hosts_edit)
        params_row.addWidget(QLabel("Ports:"))
        params_row.addWidget(self.probe_ports_edit)
        params_row.addWidget(QLabel("Serials:"))
        params_row.addWidget(self.probe_serials_edit)
        params_row.addWidget(QLabel("Bauds:"))
        params_row.addWidget(self.probe_bauds_edit)
        params_row.addWidget(QLabel("Units:"))
        params_row.addWidget(self.probe_units_edit)

        layout.addLayout(params_row)

        # --- Target register / datatype row ---
        target_row = QHBoxLayout()
        self.probe_addr_edit = QLineEdit("1")
        self.probe_addr_edit.setMaximumWidth(150)
        self.probe_datatype_combo = QComboBox()
        for dtype in (DataType.HOLDING, DataType.INPUT, DataType.COIL, DataType.DISCRETE):
            props = DATA_TYPE_PROPERTIES[dtype]
            self.probe_datatype_combo.addItem(props.label)
        self.probe_target_label = QLabel("Target address and type")
        target_row.addWidget(QLabel("Address:"))
        target_row.addWidget(self.probe_addr_edit)
        target_row.addWidget(QLabel("Type:"))
        target_row.addWidget(self.probe_datatype_combo)
        target_row.addStretch()
        layout.addLayout(target_row)

        # --- Probe config row ---
        config_row = QHBoxLayout()
        self.probe_timeout_spin = QSpinBox()
        self.probe_timeout_spin.setRange(10, 10000)
        self.probe_timeout_spin.setValue(100)
        self.probe_timeout_spin.setSuffix(" ms")
        self.probe_concurrency_spin = QSpinBox()
        self.probe_concurrency_spin.setRange(1, 1024)
        self.probe_concurrency_spin.setValue(64)
        self.probe_attempts_spin = QSpinBox()
        self.probe_attempts_spin.setRange(1, 10)
        self.probe_attempts_spin.setValue(1)
        self.probe_backoff_spin = QSpinBox()
        self.probe_backoff_spin.setRange(0, 10000)
        self.probe_backoff_spin.setValue(0)

        config_row.addWidget(QLabel("Timeout:"))
        config_row.addWidget(self.probe_timeout_spin)
        config_row.addWidget(QLabel("Concurrency:"))
        config_row.addWidget(self.probe_concurrency_spin)
        config_row.addWidget(QLabel("Attempts:"))
        config_row.addWidget(self.probe_attempts_spin)
        config_row.addWidget(QLabel("Backoff:"))
        config_row.addWidget(self.probe_backoff_spin)
        config_row.addStretch()
        layout.addLayout(config_row)

        # --- Controls ---
        controls_row = QHBoxLayout()
        self.btn_probe_start = QPushButton("Start")
        self.btn_probe_stop = QPushButton("Stop")
        self.btn_probe_stop.setEnabled(False)
        self.btn_probe_clear = QPushButton("Clear")
        self.btn_probe_export = QPushButton("Export JSON")
        controls_row.addWidget(self.btn_probe_start)
        controls_row.addWidget(self.btn_probe_stop)
        controls_row.addWidget(self.btn_probe_clear)
        controls_row.addWidget(self.btn_probe_export)
        controls_row.addStretch()
        layout.addLayout(controls_row)

        # --- Progress / summary ---
        self.probe_status_label = QLabel("Idle")
        layout.addWidget(self.probe_status_label)

        # --- Results table ---
        self.probe_table = QTableWidget()
        self.probe_table.setColumnCount(5)
        self.probe_table.setHorizontalHeaderLabels(["URI", "Params", "Status", "Summary", "RTT (ms)"])
        self.probe_table.verticalHeader().setVisible(False)
        header = self.probe_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.probe_table)

        self.probe_tab.setLayout(layout)

        # Internal state
        self._probe_task: Optional[asyncio.Task] = None
        self._probe_cancel_event: Optional[asyncio.Event] = None
        self._probe_results: List[dict] = []

        # Wire buttons
        self.btn_probe_start.clicked.connect(self.on_probe_start_clicked)
        self.btn_probe_stop.clicked.connect(self.on_probe_stop_clicked)
        self.btn_probe_clear.clicked.connect(self.on_probe_clear_clicked)
        self.btn_probe_export.clicked.connect(self.on_probe_export_clicked)


    @qasync.asyncSlot()
    async def on_probe_start_clicked(self):
        # Require the app to be disconnected so Prober can open short-lived clients
        if self._connection_uri is not None:
            QMessageBox.warning(self, "Disconnect first", "Please disconnect (click Disconnect) before probing so the Prober can open its own short-lived clients.")
            return

        if self._probe_task is not None and not self._probe_task.done():
            QMessageBox.information(self, "Probe running", "A probe run is already in progress.")
            return

        # Build parameter lists
        hosts = expand_csv_or_range(self.probe_hosts_edit.text()) or []
        ports = expand_csv_or_range(self.probe_ports_edit.text()) or []
        serials = expand_csv_or_range(self.probe_serials_edit.text()) or []
        bauds = expand_csv_or_range(self.probe_bauds_edit.text()) or []
        units = expand_csv_or_range(self.probe_units_edit.text()) or ["1"]

        # Compute Cartesian product size for safety
        h = max(1, len(hosts))
        p = max(1, len(ports))
        s = max(1, len(serials))
        u = max(1, len(units))
        total = (h * p + s * len(bauds or [9600])) * u
        if total > 5000:
            res = QMessageBox.question(self, "Large probe", f"Probe will test ~{total} combinations. Continue?", QMessageBox.Yes | QMessageBox.No)
            if res != QMessageBox.Yes:
                return

        # Prepare combinations list
        combinations = []
        # Add TCP combos
        if hosts and ports:
            for hh in hosts:
                for pp in ports:
                    for uu in units:
                        try:
                            combinations.append({"host": hh, "port": int(pp, 0), "unit": int(uu, 0)})
                        except Exception:
                            combinations.append({"host": hh, "port": pp, "unit": int(uu) if uu.isdigit() else 1})
        elif hosts:
            for hh in hosts:
                for uu in units:
                    try:
                        combinations.append({"host": hh, "port": int(self.tcp_port_edit.text() or 502), "unit": int(uu, 0)})
                    except Exception:
                        combinations.append({"host": hh, "port": int(self.tcp_port_edit.text() or 502), "unit": int(uu) if uu.isdigit() else 1})

        # Add serial combos
        if serials:
            for dev in serials:
                for bd in (bauds or [self.baud_edit.text() or "115200"]):
                    for uu in units:
                        try:
                            combinations.append({"serial": dev, "baud": int(bd, 0), "unit": int(uu, 0)})
                        except Exception:
                            combinations.append({"serial": dev, "baud": bd, "unit": int(uu) if uu.isdigit() else 1})

        # If no explicit combos built, fallback to the built URI from top-bar inputs
        if not combinations:
            combinations = [self.build_uri()]

        # Setup Prober
        timeout_ms = int(self.probe_timeout_spin.value())
        concurrency = int(self.probe_concurrency_spin.value())
        attempts = int(self.probe_attempts_spin.value())
        backoff_ms = int(self.probe_backoff_spin.value())
        datatype = self._datatype_map.get(self.probe_datatype_combo.currentIndex(), DataType.HOLDING)
        try:
            addr = int(self.probe_addr_edit.text().strip(), 0)
        except Exception:
            QMessageBox.warning(self, "Invalid address", "Enter a valid decimal or 0xHEX address.")
            return

        target = TargetSpec(datatype=datatype, address=addr)

        prober = Prober(timeout_ms=timeout_ms, concurrency=concurrency, attempts=attempts, backoff_ms=backoff_ms)

        # Clear previous results
        self.probe_table.setRowCount(0)
        self._probe_results = []

        # Create cancel event
        self._probe_cancel_event = asyncio.Event()

        def _on_result(pr):
            # Called in the same event loop; safe to update UI
            # Track all results (for accurate count), but only show ALIVE in table
            self._probe_results.append({"uri": pr.uri, "alive": pr.alive, "summary": pr.response_summary, "elapsed_ms": pr.elapsed_ms})
            if pr.alive:
                row = self.probe_table.rowCount()
                self.probe_table.insertRow(row)
                self.probe_table.setItem(row, 0, QTableWidgetItem(pr.uri))
                self.probe_table.setItem(row, 1, QTableWidgetItem(str(pr.params)))
                self.probe_table.setItem(row, 2, QTableWidgetItem("ALIVE"))
                self.probe_table.setItem(row, 3, QTableWidgetItem(str(pr.response_summary)))
                self.probe_table.setItem(row, 4, QTableWidgetItem(f"{pr.elapsed_ms:.1f}"))
            self.probe_status_label.setText(f"Tested {len(self._probe_results)} / {len(combinations)} — found {sum(1 for r in self._probe_results if r['alive'])}")

        # Disable writes during probing
        try:
            self.btn_write.setEnabled(False)
        except Exception:
            pass

        # Start probe task
        self.btn_probe_start.setEnabled(False)
        self.btn_probe_stop.setEnabled(True)
        self.probe_status_label.setText(f"Probing {len(combinations)} targets...")

        async def _run():
            try:
                await prober.run(combinations, target, on_result=_on_result, cancel_token=self._probe_cancel_event)
                alive_count = sum(1 for r in self._probe_results if r['alive'])
                self.probe_status_label.setText(f"Probe complete — tested {len(self._probe_results)}/{len(combinations)}, found {alive_count}")
            except asyncio.CancelledError:
                self.probe_status_label.setText(f"Probe cancelled — tested {len(self._probe_results)}/{len(combinations)}")
            except Exception as e:
                self.probe_status_label.setText(f"Probe error: {e}")
                QMessageBox.critical(self, "Probe Error", f"An error occurred during probe: {e}")
            finally:
                self.btn_probe_start.setEnabled(True)
                self.btn_probe_stop.setEnabled(False)
                try:
                    self.btn_write.setEnabled(True)
                except Exception:
                    pass

        self._probe_task = asyncio.create_task(_run())

    @qasync.asyncSlot()
    async def on_probe_stop_clicked(self):
        if self._probe_task is None or self._probe_task.done():
            return
        if self._probe_cancel_event:
            self._probe_cancel_event.set()
        self._probe_task.cancel()
        try:
            await self._probe_task
        except asyncio.CancelledError:
            pass
        self.probe_status_label.setText("Probe stopped")
        self.btn_probe_start.setEnabled(True)
        self.btn_probe_stop.setEnabled(False)
        try:
            self.btn_write.setEnabled(True)
        except Exception:
            pass

    def on_probe_clear_clicked(self):
        self.probe_table.setRowCount(0)
        self._probe_results = []
        self.probe_status_label.setText("Idle")

    def on_probe_export_clicked(self):
        # Simple file chooser using Qt
        from PySide6.QtWidgets import QFileDialog
        if not self._probe_results:
            QMessageBox.information(self, "No results", "No probe results to export.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save probe results", "probe_results.json", "JSON Files (*.json);;All Files (*)")
        if not path:
            return
        try:
            import json
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(self._probe_results, fh, indent=2)
            QMessageBox.information(self, "Saved", f"Wrote {len(self._probe_results)} results to {path}")
        except Exception as e:
            QMessageBox.critical(self, "Export failed", f"Failed to write results: {e}")

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
        if self._connection_uri is None:
            # Connect: store URI for use by read/write operations
            uri = self.build_uri()
            self._connection_uri = uri
            self.status_label.setText("Connected")
            self.btn_connect.setText("Disconnect")
        else:
            # Disconnect: clear stored URI
            self._connection_uri = None
            self.status_label.setText("Disconnected")
            self.btn_connect.setText("Connect")



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
        if self._connection_uri is None:
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
                value_count = self._parse_int(self.read_count_edit.text()) or 1
                if value_count <= 0:
                    QMessageBox.warning(self, "Invalid count", "Enter a positive count of values to read.")
                    return

                data_type = self._current_data_type()
                long_mode = self.read_long_checkbox.isChecked()
                if is_bit_type(data_type) and long_mode:
                    QMessageBox.warning(self, "Unsupported", "32-bit decoding is only available for register-based data types.")
                    return

                e_norm = self._normalize_endian(self.read_endian_combo.currentText(), allow_all=True)
                if e_norm is None:
                    QMessageBox.warning(self, "Invalid endian", "Select a valid endian option.")
                    return

                unit = self._parse_int(self.unit_edit.text()) or 1

                # Validate Modbus PDU limits
                if is_register_type(data_type):
                    raw_regs = value_count * (2 if long_mode else 1)
                    if raw_regs > 125:
                        QMessageBox.warning(self, "Too many registers", "Modbus only allows up to 125 registers per request.")
                        return
                else:
                    if value_count > 2000:
                        QMessageBox.warning(self, "Too many bits", "Modbus only allows up to 2000 coils/inputs per request.")
                        return

                self.status_label.setText("Reading...")

                try:
                    rows = await self._read_rows(
                        addr,
                        value_count,
                        long_mode,
                        e_norm,
                        decode,
                        data_type,
                        unit,
                        self._connection_uri,
                    )
                except RuntimeError as exc:
                    # RuntimeError from run_gui_read contains user-friendly message
                    logger.error("Read failed: %s", exc)
                    QMessageBox.critical(self, "Read Error", str(exc))
                    self.status_label.setText("Read error")
                    self.status_label.setStyleSheet("color: darkred")
                    try:
                        self.log_view.append(f"READ ERROR: {exc}")
                    except Exception:
                        pass
                    return
                except Exception as exc:
                    # Unexpected error - log full traceback
                    logger.exception("_perform_read: unexpected error")
                    QMessageBox.critical(self, "Unexpected Error", f"An unexpected error occurred: {exc}")
                    self.status_label.setText("Read error")
                    self.status_label.setStyleSheet("color: darkred")
                    return

                if not rows:
                    QMessageBox.information(self, "No data", "Read returned no values.")
                    self.read_model.update_rows([])
                    self.status_label.setText("No data")
                    return

                self.read_model.update_rows(rows)
                self.status_label.setText(f"Read {len(rows)} rows")
                try:
                    self.status_label.setStyleSheet("")
                    props = DATA_TYPE_PROPERTIES[data_type]
                    func_code = props.read_function
                    self.log_view.append(
                        f"READ: type={props.label} (func=0x{func_code:02X}) addr={addr} count={value_count} long={long_mode} rows={len(rows)}"
                    )
                except Exception:
                    pass
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
        if self._connection_uri is None:
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

        # Get unit ID and data type from connection panel
        unit = self._parse_int(self.unit_edit.text()) or 1
        data_type = self._current_data_type()

        # Validate data type is writable
        props = DATA_TYPE_PROPERTIES[data_type]
        if not props.writable:
            QMessageBox.warning(self, "Not writable", f"Data type {props.label} cannot be written.")
            return

        # Build register payload using CLI-style encoding
        try:
            regs = encode_value_to_registers(value_text, long_mode, e_norm, float_mode, signed)
        except ValueError as ve:
            QMessageBox.warning(self, "Validation error", str(ve))
            return

        # Use standalone blocking client for write
        try:
            ok = await self._write_registers(self._connection_uri, addr, unit, regs, data_type, long_mode, e_norm, float_mode, signed)
            self.write_status_label.setText("Write OK" if ok else "Write failed")
            self.write_status_label.setStyleSheet("color: darkgreen" if ok else "color: darkred")
            try:
                self.log_view.append(f"WRITE: addr={addr} unit={unit} values={regs} ok={ok}")
            except Exception:
                pass
        except RuntimeError as exc:
            # RuntimeError from run_gui_write contains user-friendly message
            logger.error("Write failed: %s", exc)
            QMessageBox.critical(self, "Write Error", str(exc))
            self.write_status_label.setText("Write error")
            self.write_status_label.setStyleSheet("color: darkred")
            try:
                self.log_view.append(f"WRITE ERROR: {exc}")
            except Exception:
                pass
        except Exception as exc:
            # Unexpected error - log full traceback
            logger.exception("_perform_write: unexpected error")
            QMessageBox.critical(self, "Unexpected Error", f"An unexpected error occurred: {exc}")
            self.write_status_label.setText("Write error")
            self.write_status_label.setStyleSheet("color: darkred")

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

    @qasync.asyncSlot()
    async def on_scan_start_clicked(self):
        """Start scanning the address range."""
        # Require connection
        if self._connection_uri is None:
            QMessageBox.warning(self, "Not connected", "Please connect first using the Connect button.")
            return

        # Parse addresses
        start_text = self.scan_start_edit.text().strip()
        end_text = self.scan_end_edit.text().strip()
        
        start_addr = self._parse_address(start_text)
        end_addr = self._parse_address(end_text)
        
        if start_addr is None or end_addr is None:
            QMessageBox.warning(self, "Invalid address", "Enter valid start and end addresses (decimal or 0xHEX).")
            return
        
        if start_addr > end_addr:
            QMessageBox.warning(self, "Invalid range", "Start address must be <= end address.")
            return

        # Get data type and unit
        data_type = self._current_data_type()
        unit = self._parse_int(self.unit_edit.text()) or 1

        # Check if already scanning
        if self._scan_task is not None and not self._scan_task.done():
            QMessageBox.information(self, "Scan in progress", "A scan is already running.")
            return

        # Update UI state
        self.btn_scan_start.setEnabled(False)
        self.btn_scan_stop.setEnabled(True)
        self.scan_status_label.setText(f"Scanning {start_addr} to {end_addr}...")

        # Start scan task
        self._scan_task = asyncio.create_task(
            self._run_scan(start_addr, end_addr, data_type, unit)
        )

    @qasync.asyncSlot()
    async def on_scan_stop_clicked(self):
        """Stop the current scan."""
        if self._scan_task is not None and not self._scan_task.done():
            self._scan_task.cancel()
            try:
                await self._scan_task
            except asyncio.CancelledError:
                pass
            self.scan_status_label.setText("Scan stopped")
        self.btn_scan_start.setEnabled(True)
        self.btn_scan_stop.setEnabled(False)

    @qasync.asyncSlot()
    async def on_scan_clear_clicked(self):
        """Clear scan results table."""
        self.scan_table.setRowCount(0)
        self.scan_status_label.setText("Ready to scan")

    async def _run_scan(self, start_addr: int, end_addr: int, data_type: DataType, unit: int):
        """Perform the scan operation in background."""
        found_count = 0
        total_count = end_addr - start_addr + 1
        
        try:
            for addr in range(start_addr, end_addr + 1):
                # Check for cancellation
                if self._scan_task.cancelled():
                    break
                
                # Update status
                progress = addr - start_addr + 1
                self.scan_status_label.setText(
                    f"Scanning {progress}/{total_count} (found {found_count})..."
                )
                
                # Attempt to read single register/coil at this address
                try:
                    await self._read_rows(
                        addr,
                        1,  # count = 1
                        False,  # long_mode = False
                        "big",  # endian
                        False,  # decode
                        data_type,
                        unit,
                        self._connection_uri,
                    )
                    
                    # Success - add to table
                    found_count += 1
                    row_position = self.scan_table.rowCount()
                    self.scan_table.insertRow(row_position)
                    self.scan_table.setItem(row_position, 0, QTableWidgetItem(str(addr)))
                    self.scan_table.setItem(row_position, 1, QTableWidgetItem(hex(addr)))
                    self.scan_table.setItem(row_position, 2, QTableWidgetItem("Readable"))
                    
                    # Color the row green
                    for col in range(3):
                        item = self.scan_table.item(row_position, col)
                        if item:
                            item.setBackground(QBrush(QColor(200, 255, 200)))
                    
                except Exception:
                    # Silently ignore errors (address not readable)
                    pass
                
                # Small delay to keep UI responsive
                await asyncio.sleep(0.01)
            
            # Scan complete
            self.scan_status_label.setText(
                f"Scan complete. Found {found_count} readable address(es) out of {total_count}."
            )
            
        except asyncio.CancelledError:
            self.scan_status_label.setText("Scan cancelled")
            raise
        except Exception as e:
            self.scan_status_label.setText(f"Scan error: {e}")
            QMessageBox.critical(self, "Scan Error", f"An error occurred during scan: {e}")
        finally:
            self.btn_scan_start.setEnabled(True)
            self.btn_scan_stop.setEnabled(False)

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

    async def _read_rows(
        self,
        addr: int,
        value_count: int,
        long_mode: bool,
        endian: str,
        decode: bool,
        data_type: DataType,
        unit: int,
        uri: Optional[str] = None,
    ) -> Optional[List[ReadRow]]:
        """Read values via standalone blocking client and return table rows.
        
        Raises RuntimeError with user-friendly message on errors.
        """
        if uri is None:
            uri = self._connection_uri or self.build_uri()
        return await asyncio.to_thread(
            run_gui_read,
            uri,
            addr,
            value_count,
            long_mode,
            endian,
            decode,
            data_type,
            unit,
        )

    async def _write_registers(self, uri: str, addr: int, unit: int, values: List[int], data_type: DataType, long_mode: bool, endian: str, float_mode: bool, signed: bool) -> bool:
        """Write values using standalone blocking client.
        
        Raises RuntimeError with user-friendly message on errors.
        """
        # Construct value text from values list
        if len(values) == 1:
            value_text = str(values[0])
        else:
            # For multi-register, use first value (caller should have encoded properly)
            value_text = str(values[0] if values else 0)
        
        ok, _ = await asyncio.to_thread(run_gui_write, uri, addr, long_mode, endian, float_mode, signed, value_text, data_type, unit)
        return ok



    def _compute_decoding_rows(self, regs: List[int]) -> List[dict]:
        """Return a list of decoding dicts for each endianness.

        Each dict contains keys: Format, Hex(s), UInt16, Int16, Float16, Hex32, Int32, Float32

        Uses the shared decoding module for consistent behavior with CLI.
        """
        # Delegate to shared decoding helpers
        if not regs:
            return []
        is_32bit = len(regs) >= 2
        result = decode_registers(regs, long_mode=is_32bit, include_all_formats=True)
        return decode_to_table_dict(result)

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
                # Hide 16-bit columns and show 32-bit columns
                table.setColumnHidden(1, True)
                table.setColumnHidden(2, True)
                table.setColumnHidden(3, True)
                table.setColumnHidden(4, True)
                table.setColumnHidden(5, False)
                table.setColumnHidden(6, False)
                table.setColumnHidden(7, False)
                table.setColumnHidden(8, False)
            else:
                # Show 16-bit columns and hide 32-bit columns
                table.setColumnHidden(1, False)
                table.setColumnHidden(2, False)
                table.setColumnHidden(3, False)
                table.setColumnHidden(4, False)
                table.setColumnHidden(5, True)
                table.setColumnHidden(6, True)
                table.setColumnHidden(7, True)
                table.setColumnHidden(8, True)

            # Populate rows from decoding_rows
            table.setRowCount(len(decoding_rows))
            for r_idx, d in enumerate(decoding_rows):
                table.setItem(r_idx, 0, QTableWidgetItem(d.get('Format', '')))
                table.setItem(r_idx, 1, QTableWidgetItem(d.get('Hex', '')))
                table.setItem(r_idx, 2, QTableWidgetItem(d.get('UInt16', '')))
                table.setItem(r_idx, 3, QTableWidgetItem(d.get('Int16', '')))
                table.setItem(r_idx, 4, QTableWidgetItem(d.get('Float16', '')))
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
        if self._connection_uri is None:
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
        data_type = self._current_data_type()
        self.monitor_model.set_config(regs_to_read, long_mode, e_norm, data_type)

        # Update UI state
        self.btn_monitor_start.setEnabled(False)
        self.btn_monitor_stop.setEnabled(True)
        self.monitor_status_label.setText(f"Monitoring address {addr} every {interval_ms}ms...")

        # Start the polling task
        self._monitor_task = asyncio.create_task(
            self._monitor_polling_loop(addr, count, regs_to_read, long_mode, e_norm, unit, interval_sec, data_type)
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

    async def _monitor_polling_loop(self, addr: int, count: int, regs_to_read: int, long_mode: bool, endian: str, unit: int, interval_sec: float, data_type: DataType):
        """Async polling loop that reads from the device at configured interval."""
        poll_count = 0
        while True:
            try:
                poll_count += 1
                timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]

                # Attempt to read registers using standalone blocking client
                try:
                    rows = await self._read_rows(
                        addr,
                        count,
                        long_mode,
                        endian,
                        False,
                        data_type,
                        unit,
                        self._connection_uri,
                    )
                    regs = [int(r.int_value) & 0xFFFF for r in rows] if rows else []

                    # Create one sample with all raw register values for this interval
                    sample = MonitorSample(
                        timestamp=timestamp,
                        raw_registers=list(regs),
                        address_start=addr,
                        unit_id=unit,
                        data_type=data_type,
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

                except RuntimeError as exc:
                    # RuntimeError from run_gui_read contains user-friendly message
                    logger.warning("Monitor poll error: %s", exc)
                    error_sample = MonitorSample(
                        timestamp=timestamp,
                        raw_registers=[],
                        address_start=addr,
                        unit_id=unit,
                        data_type=data_type,
                        error=str(exc),
                    )
                    self.monitor_model.add_sample(error_sample)
                except Exception as exc:
                    # Unexpected error - log full traceback but continue monitoring
                    logger.exception("Monitor poll unexpected error")
                    error_sample = MonitorSample(
                        timestamp=timestamp,
                        raw_registers=[],
                        address_start=addr,
                        unit_id=unit,
                        data_type=data_type,
                        error=f"Unexpected error: {exc}",
                    )
                    self.monitor_model.add_sample(error_sample)

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
