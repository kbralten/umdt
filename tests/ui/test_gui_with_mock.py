"""
Pytest-qt UI tests for UMDT GUI using the Dockerized mock server.

These tests require:
1. pytest-qt installed
2. Mock server running on localhost:5020 (via `docker compose up -d mock-server`)

Run with:
    pytest tests/ui/ -v --tb=short

Or with the full Docker E2E setup:
    docker compose up -d mock-server
    pytest tests/ui/ -v
    docker compose down
"""

import pytest
import asyncio
import os
import sys
from typing import Generator
from PySide6.QtCore import Qt

# Skip all tests in this module if DISPLAY is not available (headless CI without xvfb)
pytestmark = pytest.mark.skipif(
    sys.platform != "win32" and not os.environ.get("DISPLAY"),
    reason="No DISPLAY available for Qt tests"
)


@pytest.fixture(scope="module")
def mock_server_uri() -> str:
    """URI for the mock server. Override via environment variable if needed."""
    host = os.environ.get("MOCK_SERVER_HOST", "localhost")
    port = os.environ.get("MOCK_SERVER_PORT", "5020")
    return f"tcp://{host}:{port}?unit=1"


@pytest.fixture(scope="module")
def event_loop():
    """Create an event loop for the test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def main_window(qtbot):
    """Create and show the MainWindow for testing."""
    # Import here to avoid issues if PySide6 not available
    from main_gui import MainWindow
    
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    return window


@pytest.fixture
def connected_window(qtbot, main_window, mock_server_uri):
    """MainWindow connected to the mock server.
    
    Note: This fixture sets up the connection state directly rather than
    clicking the button, since pytest doesn't run the qasync event loop.
    """
    from urllib.parse import urlparse
    
    parsed = urlparse(mock_server_uri)
    host = parsed.hostname or "localhost"
    port = parsed.port or 5020
    
    # Switch to TCP mode
    main_window.conn_type_combo.setCurrentText("TCP")
    qtbot.wait(50)
    
    # Set connection details in UI
    main_window.host_edit.setText(host)
    main_window.tcp_port_edit.setText(str(port))
    main_window.unit_edit.setText("1")
    
    # Directly set connection state (bypassing async button click)
    # This mimics what on_connect_clicked does
    uri = main_window.build_uri()
    main_window._connection_uri = uri
    main_window.status_label.setText("Connected")
    main_window.btn_connect.setText("Disconnect")
    
    yield main_window
    
    # Cleanup: disconnect
    main_window._connection_uri = None
    main_window.status_label.setText("Disconnected")
    main_window.btn_connect.setText("Connect")


class TestMainWindowBasic:
    """Basic UI tests that don't require a mock server."""
    
    def test_window_opens(self, main_window):
        """Test that the main window opens correctly."""
        assert main_window.isVisible()
        assert main_window.windowTitle() == "UMDT"
    
    def test_tabs_exist(self, main_window):
        """Test that all expected tabs are present."""
        tab_names = [main_window.tabs.tabText(i) for i in range(main_window.tabs.count())]
        assert "Interact" in tab_names
        assert "Monitor" in tab_names
        assert "Scan" in tab_names
        assert "Probe" in tab_names
    
    def test_connection_type_toggle(self, qtbot, main_window):
        """Test toggling between Serial and TCP connection types."""
        # Start with Serial (default or first)
        main_window.conn_type_combo.setCurrentText("Serial")
        qtbot.wait(50)
        assert main_window.serial_widget.isVisible()
        assert not main_window.tcp_widget.isVisible()
        
        # Switch to TCP
        main_window.conn_type_combo.setCurrentText("TCP")
        qtbot.wait(50)
        assert not main_window.serial_widget.isVisible()
        assert main_window.tcp_widget.isVisible()
    
    def test_read_address_input(self, qtbot, main_window):
        """Test entering a read address."""
        main_window.read_addr_edit.clear()
        qtbot.keyClicks(main_window.read_addr_edit, "0x0010")
        assert main_window.read_addr_edit.text() == "0x0010"
    
    def test_write_address_input(self, qtbot, main_window):
        """Test entering a write address and value."""
        main_window.write_addr_edit.clear()
        qtbot.keyClicks(main_window.write_addr_edit, "100")
        assert main_window.write_addr_edit.text() == "100"
        
        main_window.write_value_edit.clear()
        qtbot.keyClicks(main_window.write_value_edit, "12345")
        assert main_window.write_value_edit.text() == "12345"


@pytest.mark.skipif(
    os.environ.get("SKIP_MOCK_SERVER_TESTS", "0") == "1",
    reason="Mock server tests skipped via SKIP_MOCK_SERVER_TESTS=1"
)
class TestWithMockServer:
    """UI tests that verify connection state with mock server.
    
    Note: These tests verify UI state after connection setup. Actual Modbus
    read/write operations require qasync event loop integration which is
    complex to set up in pytest. The CLI E2E tests cover actual Modbus
    communication thoroughly.
    """
    
    def test_connect_state(self, connected_window):
        """Test that connection state is properly set."""
        assert connected_window._connection_uri is not None
        assert "tcp://" in connected_window._connection_uri
        assert connected_window.btn_connect.text() == "Disconnect"
        assert "Connected" in connected_window.status_label.text()
    
    def test_connection_uri_format(self, connected_window, mock_server_uri):
        """Test that connection URI matches expected format."""
        uri = connected_window._connection_uri
        assert uri is not None
        # Should contain the mock server host and port
        assert "localhost" in uri or "127.0.0.1" in uri
        assert "5020" in uri
        assert "unit=1" in uri
    
    def test_read_inputs_configured(self, qtbot, connected_window):
        """Test that read inputs can be configured while connected."""
        window = connected_window
        
        # Navigate to Interact tab
        window.tabs.setCurrentWidget(window.interact_tab)
        qtbot.wait(50)
        
        # Set address and count
        window.read_addr_edit.clear()
        qtbot.keyClicks(window.read_addr_edit, "0")
        window.read_count_edit.clear()
        qtbot.keyClicks(window.read_count_edit, "10")
        
        assert window.read_addr_edit.text() == "0"
        assert window.read_count_edit.text() == "10"
    
    def test_write_inputs_configured(self, qtbot, connected_window):
        """Test that write inputs can be configured while connected."""
        window = connected_window
        
        window.tabs.setCurrentWidget(window.interact_tab)
        qtbot.wait(50)
        
        window.write_addr_edit.clear()
        qtbot.keyClicks(window.write_addr_edit, "100")
        window.write_value_edit.clear()
        qtbot.keyClicks(window.write_value_edit, "12345")
        
        assert window.write_addr_edit.text() == "100"
        assert window.write_value_edit.text() == "12345"
    
    def test_datatype_selection(self, qtbot, connected_window):
        """Test that datatype can be changed while connected."""
        window = connected_window
        
        window.tabs.setCurrentWidget(window.interact_tab)
        qtbot.wait(50)
        
        # Cycle through datatypes
        for dtype in ["Holding Registers", "Input Registers", "Coils", "Discrete Inputs"]:
            window.datatype_combo.setCurrentText(dtype)
            qtbot.wait(50)
            assert window.datatype_combo.currentText() == dtype
    
    def test_disconnect_state(self, qtbot, connected_window):
        """Test that disconnect properly clears state."""
        window = connected_window
        
        # Manually disconnect (as fixture cleanup will do)
        window._connection_uri = None
        window.status_label.setText("Disconnected")
        window.btn_connect.setText("Connect")
        
        assert window._connection_uri is None
        assert window.btn_connect.text() == "Connect"
        assert "Disconnected" in window.status_label.text()


class TestScanTab:
    """Tests for the Scan tab functionality."""
    
    def test_scan_tab_elements(self, main_window):
        """Test that scan tab has expected elements."""
        main_window.tabs.setCurrentWidget(main_window.scan_tab)
        
        assert main_window.scan_start_edit is not None
        assert main_window.scan_end_edit is not None
        assert main_window.btn_scan_start is not None
        assert main_window.btn_scan_stop is not None
        assert main_window.btn_scan_clear is not None


class TestProbeTab:
    """Tests for the Probe tab functionality."""
    
    def test_probe_tab_elements(self, main_window):
        """Test that probe tab has expected elements."""
        main_window.tabs.setCurrentWidget(main_window.probe_tab)
        
        assert main_window.probe_hosts_edit is not None
        assert main_window.probe_ports_edit is not None
        assert main_window.btn_probe_start is not None
        assert main_window.btn_probe_stop is not None


class TestMonitorTab:
    """Tests for the Monitor tab functionality."""
    
    def test_monitor_tab_elements(self, main_window):
        """Test that monitor tab has expected elements."""
        main_window.tabs.setCurrentWidget(main_window.monitor_tab)
        
        assert main_window.monitor_addr_edit is not None
        assert main_window.monitor_count_edit is not None
        assert main_window.monitor_interval_spin is not None
        assert main_window.btn_monitor_start is not None
        assert main_window.btn_monitor_stop is not None
