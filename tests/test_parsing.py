"""Tests for umdt.utils.parsing module."""

import pytest
from umdt.utils.parsing import (
    expand_csv_or_range,
    expand_int_range,
    parse_host_port,
    parse_serial_baud,
    normalize_serial_port,
)


class TestExpandCsvOrRange:
    """Tests for expand_csv_or_range function."""

    def test_empty_input_returns_empty_list(self):
        assert expand_csv_or_range(None) == []
        assert expand_csv_or_range("") == []
        assert expand_csv_or_range("   ") == []

    def test_single_value(self):
        assert expand_csv_or_range("5") == ["5"]
        assert expand_csv_or_range("100") == ["100"]

    def test_csv_values(self):
        assert expand_csv_or_range("1,2,3") == ["1", "2", "3"]
        assert expand_csv_or_range("10, 20, 30") == ["10", "20", "30"]

    def test_simple_range(self):
        assert expand_csv_or_range("1-5") == ["1", "2", "3", "4", "5"]
        assert expand_csv_or_range("10-12") == ["10", "11", "12"]

    def test_reverse_range(self):
        assert expand_csv_or_range("5-1") == ["5", "4", "3", "2", "1"]
        assert expand_csv_or_range("3-1") == ["3", "2", "1"]

    def test_combined_csv_and_range(self):
        assert expand_csv_or_range("1,5-8,10") == ["1", "5", "6", "7", "8", "10"]
        assert expand_csv_or_range("1-3,10,15-17") == ["1", "2", "3", "10", "15", "16", "17"]

    def test_hex_values(self):
        # Single hex values pass through as strings (semantics preservation)
        assert expand_csv_or_range("0x10") == ["0x10"]
        assert expand_csv_or_range("0x10,0x20") == ["0x10", "0x20"]

    def test_hex_range(self):
        # Hex ranges get expanded to decimal strings
        assert expand_csv_or_range("0x10-0x12") == ["16", "17", "18"]

    def test_non_numeric_passthrough(self):
        assert expand_csv_or_range("COM1,COM3") == ["COM1", "COM3"]
        assert expand_csv_or_range("host1,host2") == ["host1", "host2"]

    def test_mixed_numeric_and_strings(self):
        # Non-numeric ranges pass through as-is
        result = expand_csv_or_range("1,COM1-COM3,5")
        assert result == ["1", "COM1-COM3", "5"]

    def test_leading_dash_not_range(self):
        # Negative numbers should not be treated as ranges
        assert expand_csv_or_range("-5") == ["-5"]

    def test_trailing_dash_not_range(self):
        # Trailing dash should not be treated as range
        assert expand_csv_or_range("5-") == ["5-"]

    def test_whitespace_handling(self):
        assert expand_csv_or_range("  1  ,  2  ,  3  ") == ["1", "2", "3"]
        assert expand_csv_or_range("  1-3  ") == ["1", "2", "3"]


class TestExpandIntRange:
    """Tests for expand_int_range function."""

    def test_empty_input_returns_empty_list(self):
        assert expand_int_range(None) == []
        assert expand_int_range("") == []

    def test_single_value(self):
        assert expand_int_range("5") == [5]
        assert expand_int_range("100") == [100]

    def test_csv_values(self):
        assert expand_int_range("1,2,3") == [1, 2, 3]

    def test_simple_range(self):
        assert expand_int_range("1-5") == [1, 2, 3, 4, 5]

    def test_hex_values(self):
        assert expand_int_range("0x10") == [16]
        assert expand_int_range("0x10,0x20") == [16, 32]
        assert expand_int_range("0x10-0x12") == [16, 17, 18]

    def test_non_numeric_skipped(self):
        # Non-numeric values are silently skipped
        assert expand_int_range("1,COM1,5") == [1, 5]
        assert expand_int_range("abc") == []

    def test_combined_csv_and_range(self):
        assert expand_int_range("1,5-8,10") == [1, 5, 6, 7, 8, 10]


class TestParseHostPort:
    """Tests for parse_host_port function."""

    def test_host_with_port(self):
        assert parse_host_port("192.168.1.1:502") == ("192.168.1.1", 502)
        assert parse_host_port("localhost:5020") == ("localhost", 5020)

    def test_host_without_port(self):
        assert parse_host_port("192.168.1.1") == ("192.168.1.1", 502)
        assert parse_host_port("localhost") == ("localhost", 502)

    def test_custom_default_port(self):
        assert parse_host_port("192.168.1.1", default_port=5020) == ("192.168.1.1", 5020)

    def test_whitespace_stripped(self):
        assert parse_host_port("  192.168.1.1:502  ") == ("192.168.1.1", 502)

    def test_invalid_port_raises(self):
        with pytest.raises(ValueError, match="Invalid port"):
            parse_host_port("192.168.1.1:abc")


class TestParseSerialBaud:
    """Tests for parse_serial_baud function."""

    def test_port_with_baud(self):
        assert parse_serial_baud("COM5:115200") == ("COM5", 115200)
        assert parse_serial_baud("/dev/ttyUSB0:9600") == ("/dev/ttyUSB0", 9600)

    def test_port_without_baud(self):
        assert parse_serial_baud("COM5") == ("COM5", 9600)

    def test_custom_default_baud(self):
        assert parse_serial_baud("COM5", default_baud=115200) == ("COM5", 115200)

    def test_whitespace_stripped(self):
        assert parse_serial_baud("  COM5:9600  ") == ("COM5", 9600)

    def test_invalid_baud_raises(self):
        with pytest.raises(ValueError, match="Invalid baud"):
            parse_serial_baud("COM5:abc")


class TestNormalizeSerialPort:
    """Tests for normalize_serial_port function."""

    def test_removes_leading_slashes(self):
        assert normalize_serial_port("/COM3") == "COM3"
        assert normalize_serial_port("//COM5") == "COM5"

    def test_preserves_normal_port(self):
        assert normalize_serial_port("COM5") == "COM5"

    def test_empty_string(self):
        assert normalize_serial_port("") == ""

    def test_linux_paths(self):
        # Linux paths lose first slash but that's intentional for urlparse results
        assert normalize_serial_port("/dev/ttyUSB0") == "dev/ttyUSB0"
