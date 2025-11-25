import types
import pytest

from unittest.mock import Mock

from umdt.utils import modbus_compat as mc


def test_call_read_method_with_unit_kw():
    def read_fn(address, count, unit=1):
        return (address, count, unit)

    client = types.SimpleNamespace(read_holding_registers=read_fn)
    res = mc.call_read_method(client, 'read_holding_registers', 10, 2, 5)
    assert res == (10, 2, 5)


def test_call_read_method_with_slave_kw():
    def read_fn(address, count, slave=1):
        return (address, count, slave)

    client = types.SimpleNamespace(read_holding_registers=read_fn)
    res = mc.call_read_method(client, 'read_holding_registers', 1, 4, 3)
    assert res == (1, 4, 3)


def test_call_read_method_positional_unit():
    def read_fn(address, count, unit):
        return (address, count, unit)

    client = types.SimpleNamespace(read_holding_registers=read_fn)
    res = mc.call_read_method(client, 'read_holding_registers', 7, 1, 9)
    assert res == (7, 1, 9)


def test_call_write_method_with_varied_kw():
    def write_fn(address, values, slave=1):
        return (address, values, slave)

    client = types.SimpleNamespace(write_registers=write_fn)
    res = mc.call_write_method(client, 'write_registers', 5, [1, 2, 3], 2)
    assert res == (5, [1, 2, 3], 2)


def test_create_client_uses_imported_constructors(monkeypatch):
    # Create fake constructors that record kwargs
    class FakeTcp:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeSerial:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setattr(mc, '_import_clients', lambda: (FakeTcp, FakeSerial))

    tcp = mc.create_client(kind='tcp', host='127.0.0.1', port=502, timeout=0.5)
    assert isinstance(tcp, FakeTcp)
    assert tcp.kwargs['host'] == '127.0.0.1'
    assert tcp.kwargs['port'] == 502
    assert tcp.kwargs['timeout'] == 0.5

    serial = mc.create_client(kind='serial', serial_port='COM3', baudrate=9600, timeout=1.0, retries=0)
    assert isinstance(serial, FakeSerial)
    assert serial.kwargs['port'] == 'COM3' or serial.kwargs.get('port') == 'COM3'
    assert serial.kwargs['baudrate'] == 9600
    assert serial.kwargs['retries'] == 0


def test_close_client_calls_close(monkeypatch):
    mock = Mock()
    mock.close = Mock()
    mc.close_client(mock)
    mock.close.assert_called_once()


def test_close_client_socket_fallback(monkeypatch):
    sock = Mock()
    client = types.SimpleNamespace(close=None, socket=sock)
    # Ensure close_client uses socket.close when close not callable
    mc.close_client(client)
    sock.close.assert_called_once()


def test_convenience_read_wrappers_call_methods():
    called = {}

    def read_holding(address, count, unit=1):
        called['holding'] = (address, count, unit)
        return 'ok'

    client = types.SimpleNamespace(read_holding_registers=read_holding)
    res = mc.read_holding_registers(client, 2, 3, unit=4)
    assert res == 'ok'
    assert called['holding'] == (2, 3, 4)
