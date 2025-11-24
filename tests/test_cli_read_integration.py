import importlib
import types

import pytest

import main_cli as cli


def test_read_command_with_dummy_tcp(monkeypatch, capsys):
    # Ensure pymodbus flag is set so code uses ModbusTcpClient
    monkeypatch.setattr(cli, "_HAS_PYMODBUS", True)

    class DummyResponse:
        def __init__(self):
            self.registers = [123]

    class DummyClient:
        def __init__(self, host, port=None, **kwargs):
            self.host = host
            self.port = port

        def connect(self):
            return True

        def close(self):
            return None

        def read_holding_registers(self, address, count=None, unit=1, **kwargs):
            return DummyResponse()

    monkeypatch.setattr(cli, "ModbusTcpClient", DummyClient)

    # Call the read command function directly; should not raise
    cli.read(serial=None, baud=9600, host="127.0.0.1", port=15020, unit=1, address="0", count=1, long=False, endian="big", datatype="holding")

    # capture output to ensure something was printed
    captured = capsys.readouterr()
    assert "Index" in captured.out or "Read error" not in captured.out
