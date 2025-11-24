from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from typing import Optional

from PySide6 import QtCore, QtWidgets, QtGui
import qasync

from umdt.core.data_types import DataType
from umdt.mock_server import MockDevice, TransportCoordinator, load_config
from umdt.mock_server.models import RegisterRule, ResponseMode, RegisterGroup
from umdt.mock_server.config import MockServerConfig, TransportConfig
try:
    import yaml  # type: ignore
except Exception:
    yaml = None

try:
    # optional import; used only to discover serial ports if pyserial is installed
    from serial.tools import list_ports  # type: ignore
except Exception:
    list_ports = None


class GroupTableModel(QtCore.QAbstractTableModel):
    headers = ["Name", "Type", "Start", "Length", "Writable"]

    def __init__(self) -> None:
        super().__init__()
        self._rows: list[tuple[str, str, int, int, bool]] = []

    def set_groups(self, rows: list[tuple[str, str, int, int, bool]]) -> None:
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def rowCount(self, parent: QtCore.QModelIndex = QtCore.QModelIndex()) -> int:  # type: ignore[override]
        return len(self._rows)

    def columnCount(self, parent: QtCore.QModelIndex = QtCore.QModelIndex()) -> int:  # type: ignore[override]
        return len(self.headers)

    def data(self, index: QtCore.QModelIndex, role: int = QtCore.Qt.DisplayRole):  # type: ignore[override]
        display_role = QtCore.Qt.ItemDataRole.DisplayRole
        edit_role = QtCore.Qt.ItemDataRole.EditRole
        if not index.isValid() or role not in (display_role, edit_role):
            return None
        row = self._rows[index.row()]
        value = row[index.column()]
        if isinstance(value, bool):
            return "Yes" if value else "No"
        return value

    def headerData(self, section: int, orientation: QtCore.Qt.Orientation, role: int = QtCore.Qt.DisplayRole):  # type: ignore[override]
        if orientation == QtCore.Qt.Orientation.Horizontal and role == QtCore.Qt.ItemDataRole.DisplayRole:
            return self.headers[section]
        return super().headerData(section, orientation, role)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("UMDT Mock Modbus Server")
        self.resize(900, 600)

        # Ensure the main window and taskbar use the mock icon when available
        try:
            resource_base = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
            ico_path = resource_base / "umdt_mock.ico"
            if ico_path.exists():
                icon = QtGui.QIcon(str(ico_path))
                self.setWindowIcon(icon)
                app = QtWidgets.QApplication.instance()
                if app is not None:
                    app.setWindowIcon(icon)
        except Exception:
            pass

        self.device: Optional[MockDevice] = None
        self.coordinator: Optional[TransportCoordinator] = None
        self._event_task: Optional[asyncio.Task] = None

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)

        # Add config panel
        layout.addWidget(self._build_config_panel(), 0)

        # Create tabbed interface for register groups, rules, faults, and events
        self.tabs = QtWidgets.QTabWidget()
        self.tabs.addTab(self._build_group_panel(), "Register Groups")
        self.tabs.addTab(self._build_rule_panel(), "Manual Write / Rules")
        self.tabs.addTab(self._build_fault_panel(), "Fault Injection")
        self.tabs.addTab(self._build_event_panel(), "Events")
        layout.addWidget(self.tabs, 1)

    def _build_config_panel(self) -> QtWidgets.QGroupBox:
        box = QtWidgets.QGroupBox("Configuration")
        grid = QtWidgets.QGridLayout(box)

        self.config_edit = QtWidgets.QLineEdit()
        browse_btn = QtWidgets.QPushButton("Browse")
        browse_btn.clicked.connect(self._choose_config)
        save_btn = QtWidgets.QPushButton("Save Config")
        save_btn.clicked.connect(self._save_config)

        self.unit_id_spin = QtWidgets.QSpinBox()
        self.unit_id_spin.setRange(0, 247)
        self.unit_id_spin.setValue(1)
        self.unit_id_spin.setToolTip("Modbus unit/slave ID (0-247). Server will ignore requests to other unit IDs.")

        self.transport_combo = QtWidgets.QComboBox()
        self.transport_combo.addItems(["TCP", "Serial"])
        self.transport_combo.currentTextChanged.connect(self._on_transport_changed)
        self.tcp_host_edit = QtWidgets.QLineEdit("127.0.0.1")
        self.tcp_port_spin = QtWidgets.QSpinBox()
        self.tcp_port_spin.setRange(1, 65535)
        self.tcp_port_spin.setValue(15020)

        # Serial controls: port combo (discovered) and baud combo (common rates)
        self.serial_port_combo = QtWidgets.QComboBox()
        self.serial_baud_combo = QtWidgets.QComboBox()
        common_bauds = [300, 1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200]
        for b in common_bauds:
            self.serial_baud_combo.addItem(str(b), userData=b)
        # default
        idx = self.serial_baud_combo.findText("9600")
        if idx >= 0:
            self.serial_baud_combo.setCurrentIndex(idx)
        # populate serial ports lazily
        self._discover_serial_ports()

        # start/stop buttons
        self.start_btn = QtWidgets.QPushButton("Start Server")
        self.stop_btn = QtWidgets.QPushButton("Stop")
        self.stop_btn.setEnabled(False)

        self.start_btn.clicked.connect(lambda: asyncio.create_task(self.start_server()))
        self.stop_btn.clicked.connect(lambda: asyncio.create_task(self.stop_server()))

        grid.addWidget(QtWidgets.QLabel("Config file"), 0, 0)
        grid.addWidget(self.config_edit, 0, 1)
        grid.addWidget(browse_btn, 0, 2)
        grid.addWidget(save_btn, 0, 3)

        grid.addWidget(QtWidgets.QLabel("Unit ID"), 1, 0)
        grid.addWidget(self.unit_id_spin, 1, 1)

        grid.addWidget(QtWidgets.QLabel("Transport"), 2, 0)
        grid.addWidget(self.transport_combo, 2, 1)

        tcp_row = QtWidgets.QHBoxLayout()
        tcp_row.addWidget(QtWidgets.QLabel("Host"))
        tcp_row.addWidget(self.tcp_host_edit)
        tcp_row.addWidget(QtWidgets.QLabel("Port"))
        tcp_row.addWidget(self.tcp_port_spin)
        grid.addLayout(tcp_row, 3, 0, 1, 3)

        serial_row = QtWidgets.QHBoxLayout()
        serial_row.addWidget(QtWidgets.QLabel("Serial Port"))
        serial_row.addWidget(self.serial_port_combo)
        serial_row.addWidget(QtWidgets.QLabel("Baud"))
        serial_row.addWidget(self.serial_baud_combo)
        grid.addLayout(serial_row, 4, 0, 1, 3)

        # Note to make mutual exclusivity explicit
        self.transport_note = QtWidgets.QLabel("TCP and Serial are mutually exclusive — select one.")
        self.transport_note.setStyleSheet("color: gray; font-style: italic")
        grid.addWidget(self.transport_note, 2, 2)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addWidget(self.start_btn)
        btn_row.addWidget(self.stop_btn)
        grid.addLayout(btn_row, 5, 0, 1, 3)

        # Ensure controls reflect the default transport selection (defaults to TCP)
        self._on_transport_changed(self.transport_combo.currentText())

        return box

    def _discover_serial_ports(self) -> None:
        """Populate `self.serial_port_combo` with discovered serial ports (if available)."""
        self.serial_port_combo.clear()
        if list_ports is None:
            # no pyserial available; provide common placeholder names on Windows
            self.serial_port_combo.addItems(["COM1", "COM2", "COM3", "COM4", "COM5"])
            return
        try:
            ports = list_ports.comports()
            items = [p.device for p in ports]
            if not items:
                # fallback placeholders
                items = ["COM1", "COM2", "COM3"]
            self.serial_port_combo.addItems(items)
        except Exception:
            self.serial_port_combo.addItems(["COM1", "COM2", "COM3"])
        return None

    def _on_transport_changed(self, text: str) -> None:
        """Enable TCP controls when TCP selected, otherwise enable Serial controls."""
        is_tcp = (text.lower() == "tcp")
        self.tcp_host_edit.setEnabled(is_tcp)
        self.tcp_port_spin.setEnabled(is_tcp)
        # new widget names for serial controls
        self.serial_port_combo.setEnabled(not is_tcp)
        self.serial_baud_combo.setEnabled(not is_tcp)

    def _build_group_panel(self) -> QtWidgets.QWidget:
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)
        self.group_model = GroupTableModel()
        self.group_table = QtWidgets.QTableView()
        # make the table more usable by default
        self.group_table.setMinimumHeight(240)
        self.group_table.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)
        self.group_table.setModel(self.group_model)
        self.group_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.group_table)

        btn_row = QtWidgets.QHBoxLayout()
        add_btn = QtWidgets.QPushButton("Add Group")
        remove_btn = QtWidgets.QPushButton("Remove Selected")
        add_btn.clicked.connect(self._on_add_group)
        remove_btn.clicked.connect(self._on_remove_group)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(remove_btn)
        layout.addLayout(btn_row)

        # maintain current groups as RegisterGroup objects
        self._groups: list[RegisterGroup] = []
        return widget

    def _on_add_group(self) -> None:
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Add Register Group")
        form = QtWidgets.QFormLayout(dialog)
        name_edit = QtWidgets.QLineEdit("group")
        type_combo = QtWidgets.QComboBox()
        # DataType values (strings)
        from umdt.core.data_types import DataType as _DT
        for dt in _DT:
            type_combo.addItem(dt.value, userData=dt)
        start_spin = QtWidgets.QSpinBox()
        start_spin.setRange(0, 65535)
        length_spin = QtWidgets.QSpinBox()
        length_spin.setRange(1, 10000)
        writable_cb = QtWidgets.QCheckBox()
        writable_cb.setChecked(True)
        form.addRow("Name", name_edit)
        form.addRow("Type", type_combo)
        form.addRow("Start", start_spin)
        form.addRow("Length", length_spin)
        form.addRow("Writable", writable_cb)
        btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        btns.accepted.connect(dialog.accept)
        btns.rejected.connect(dialog.reject)
        form.addRow(btns)
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        name = name_edit.text().strip() or "group"
        dt: _DT = type_combo.currentData()
        grp = RegisterGroup(name=name, data_type=dt, start=int(start_spin.value()), length=int(length_spin.value()), writable=bool(writable_cb.isChecked()))
        self._groups.append(grp)
        self._refresh_group_table()

    def _on_remove_group(self) -> None:
        sel = self.group_table.selectionModel().selectedRows()
        if not sel:
            return
        # remove highest index first
        idxs = sorted([s.row() for s in sel], reverse=True)
        for r in idxs:
            if 0 <= r < len(self._groups):
                self._groups.pop(r)
        self._refresh_group_table()

    def _refresh_group_table(self) -> None:
        rows = [(g.name, g.data_type.value, g.start, g.length, g.writable) for g in self._groups]
        self.group_model.set_groups(rows)

    def _build_rule_panel(self) -> QtWidgets.QWidget:
        widget = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(widget)

        self.dtype_combo = QtWidgets.QComboBox()
        for dtype in DataType:
            self.dtype_combo.addItem(dtype.value, userData=dtype)

        self.address_spin = QtWidgets.QSpinBox()
        self.address_spin.setRange(0, 99999)
        self.value_edit = QtWidgets.QLineEdit("0")
        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.addItems(["write", "frozen-value", "ignore-write", "exception"])
        self.apply_btn = QtWidgets.QPushButton("Apply")
        self.apply_btn.clicked.connect(lambda: asyncio.create_task(self._apply_rule()))

        form.addRow("Data Type", self.dtype_combo)
        form.addRow("Address", self.address_spin)
        form.addRow("Value", self.value_edit)
        form.addRow("Mode", self.mode_combo)
        form.addRow(self.apply_btn)
        return widget

    def _build_fault_panel(self) -> QtWidgets.QWidget:
        widget = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(widget)

        self.latency_spin = QtWidgets.QSpinBox()
        self.latency_spin.setRange(0, 5000)
        self.jitter_spin = QtWidgets.QDoubleSpinBox()
        self.jitter_spin.setRange(0.0, 100.0)
        self.drop_spin = QtWidgets.QDoubleSpinBox()
        self.drop_spin.setRange(0.0, 100.0)
        self.bitflip_spin = QtWidgets.QDoubleSpinBox()
        self.bitflip_spin.setRange(0.0, 100.0)
        apply_btn = QtWidgets.QPushButton("Update Faults")
        apply_btn.clicked.connect(self._apply_faults)

        form.addRow("Latency (ms)", self.latency_spin)
        form.addRow("Jitter %", self.jitter_spin)
        form.addRow("Drop %", self.drop_spin)
        form.addRow("Bit flip %", self.bitflip_spin)
        form.addRow(apply_btn)
        return widget

    def _build_event_panel(self) -> QtWidgets.QWidget:
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)
        self.event_log = QtWidgets.QPlainTextEdit()
        self.event_log.setReadOnly(True)
        layout.addWidget(self.event_log)
        clear_btn = QtWidgets.QPushButton("Clear Events")
        clear_btn.clicked.connect(self.event_log.clear)
        layout.addWidget(clear_btn)
        return widget

    def _choose_config(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Select config", filter="Config (*.json *.yaml *.yml)")
        if path:
            self.config_edit.setText(path)

    def _save_config(self) -> None:
        # assemble minimal config dict from UI
        cfg_obj = {
            "unit_id": int(self.unit_id_spin.value()),
            "groups": [],
            "latency_ms": int(self.latency_spin.value()),
            "latency_jitter_pct": float(self.jitter_spin.value()),
            "faults": {},
            "transport": {},
        }
        for g in self._groups:
            cfg_obj["groups"].append({
                "name": g.name,
                "type": g.data_type.value,
                "start": int(g.start),
                "length": int(g.length),
                "writable": bool(g.writable),
            })
        if self.transport_combo.currentText().lower() == "tcp":
            cfg_obj["transport"] = {"tcp_host": self.tcp_host_edit.text() or None, "tcp_port": int(self.tcp_port_spin.value())}
        else:
            cfg_obj["transport"] = {"serial_port": self.serial_port_combo.currentText() or None, "serial_baud": int(self.serial_baud_combo.currentText() or 9600)}

        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save config", filter="YAML (*.yaml *.yml);;JSON (*.json)")
        if not path:
            return
        try:
            p = Path(path)
            if p.suffix.lower() in {".yaml", ".yml"}:
                if yaml is None:
                    QtWidgets.QMessageBox.critical(self, "Save failed", "PyYAML is required to save YAML configs")
                    return
                p.write_text(yaml.safe_dump(cfg_obj, sort_keys=False), encoding="utf-8")
            else:
                import json

                p.write_text(json.dumps(cfg_obj, indent=2), encoding="utf-8")
            self.event_log.appendPlainText(f"Saved config to {path}")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Save failed", str(exc))

    async def start_server(self) -> None:
        if self.device:
            return
        text = self.config_edit.text().strip()
        cfg = None
        if text:
            path = Path(text)
            # ensure path is a file (not a directory like '.')
            if path.is_file():
                try:
                    cfg = load_config(path)
                except Exception as exc:  # pylint: disable=broad-except
                    QtWidgets.QMessageBox.critical(self, "Config error", str(exc))
                    return
            else:
                QtWidgets.QMessageBox.warning(self, "Config error", "Config path is not a file. Leave empty to start without a config.")
                return
        else:
            # build config from current UI state
            transport = None
            if self.transport_combo.currentText().lower() == "tcp":
                transport = TransportConfig(tcp_host=self.tcp_host_edit.text() or None, tcp_port=self.tcp_port_spin.value())
            else:
                transport = TransportConfig(serial_port=self.serial_port_combo.currentText() or None, serial_baud=int(self.serial_baud_combo.currentText() or 9600))
            unit_id = int(self.unit_id_spin.value())
            cfg = MockServerConfig(unit_id=unit_id, groups=list(self._groups), latency_ms=self.latency_spin.value(), latency_jitter_pct=self.jitter_spin.value(), fault_profile={}, transport=transport)

        self.device = MockDevice(cfg)
        self.coordinator = TransportCoordinator(self.device, unit_id=cfg.unit_id)
        # Update unit_id spin from loaded config
        self.unit_id_spin.setValue(cfg.unit_id)
        self.group_model.set_groups(
            [
                (group.name, group.data_type.value, group.start, group.length, group.writable)
                for group in cfg.groups
            ]
        )
        self.latency_spin.setValue(cfg.latency_ms)
        self.jitter_spin.setValue(cfg.latency_jitter_pct)

        try:
            if self.transport_combo.currentText().lower() == "tcp":
                await self.coordinator.start_tcp(self.tcp_host_edit.text() or "0.0.0.0", self.tcp_port_spin.value())
                label = f"TCP {self.tcp_host_edit.text()}:{self.tcp_port_spin.value()}"
            else:
                # serial combo and baud combo
                port = self.serial_port_combo.currentText()
                try:
                    baud = int(self.serial_baud_combo.currentText())
                except Exception:
                    baud = 9600
                await self.coordinator.start_serial(port, baud)
                label = f"Serial {port}:{baud}"
            self._event_task = asyncio.create_task(self._event_loop())
            self.event_log.appendPlainText(f"Server started on {label}")
            self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(True)
        except Exception as exc:  # pylint: disable=broad-except
            QtWidgets.QMessageBox.critical(self, "Start failed", str(exc))
            self.device = None
            self.coordinator = None

    async def stop_server(self) -> None:
        if self._event_task:
            self._event_task.cancel()
            try:
                await self._event_task
            except asyncio.CancelledError:
                pass
            self._event_task = None
        if self.coordinator:
            await self.coordinator.stop()
            self.coordinator = None
        self.device = None
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.event_log.appendPlainText("Server stopped")

    async def _event_loop(self) -> None:
        if not self.device:
            return
        try:
            while True:
                event = await self.device.diagnostics.next_event()
                self.event_log.appendPlainText(f"[{event.timestamp.isoformat()}] {event.transport}: {event.description}")
        except asyncio.CancelledError:
            return

    def _apply_faults(self) -> None:
        if not self.device:
            return
        latency = int(self.latency_spin.value())
        jitter = float(self.jitter_spin.value())
        drop = float(self.drop_spin.value())
        bitflip = float(self.bitflip_spin.value())
        self.device.diagnostics.update(
            enabled=True,
            latency_ms=latency,
            latency_jitter_pct=jitter,
            drop_rate_pct=drop,
            bit_flip_pct=bitflip,
        )
        details = f"latency={latency}ms, jitter={jitter}%, drop={drop}%, bit_flip={bitflip}%"
        self.event_log.appendPlainText(f"Updated fault profile: {details}")

    async def _apply_rule(self) -> None:
        if not self.device:
            return
        dtype = self.dtype_combo.currentData()
        address = self.address_spin.value()
        try:
            value = int(self.value_edit.text(), 0)
        except ValueError:
            QtWidgets.QMessageBox.warning(self, "Invalid value", "Enter a numeric value")
            return
        mode = self.mode_combo.currentText()
        if mode == "write":
            await self.device.write(dtype, address, [value])
            self.event_log.appendPlainText(f"Wrote {value} to {dtype.value} {address}")
            return
        from umdt.mock_server.models import RegisterRule, ResponseMode

        rule = RegisterRule(
            response_mode=ResponseMode(mode),
            forced_value=value if mode == "frozen-value" else None,
            exception_code=value if mode == "exception" else None,
            ignore_write=(mode == "ignore-write"),
        )
        await self.device.apply_rule(address, rule)
        detail = f"mode={mode}"
        if mode == "frozen-value":
            detail += f", value={value}"
        elif mode == "exception":
            detail += f", exception_code={value}"
        self.event_log.appendPlainText(f"Applied rule to {dtype.value} address {address}: {detail}")


def main() -> None:
    app = QtWidgets.QApplication(sys.argv)
    # Use a dedicated mock-server icon if available so taskbar and window show it
    try:
        ico_path = Path(__file__).resolve().parent / "umdt_mock.ico"
        if ico_path.exists():
            app_icon = QtGui.QIcon(str(ico_path))
            app.setWindowIcon(app_icon)
    except Exception:
        pass
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    window = MainWindow()
    # Ensure main window uses the same icon (affects taskbar on Windows)
    try:
        if 'app_icon' in locals():
            window.setWindowIcon(app_icon)
    except Exception:
        pass
    window.show()

    with loop:
        loop.run_forever()


if __name__ == "__main__":
    main()
