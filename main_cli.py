import typer
import asyncio
from typing import Optional, List
from umdt.core import __version__
from umdt.core.controller import CoreController
from umdt.commands.builder import CommandBuilder
from umdt.utils.ieee754 import from_bytes_to_float32, from_bytes_to_float16
from urllib.parse import urlparse, parse_qs
from umdt.core.data_types import (
    DATA_TYPE_PROPERTIES,
    DataType,
    is_bit_type,
    is_register_type,
    parse_data_type,
)
from umdt.utils.modbus_compat import (
    create_client,
    close_client,
)
from umdt.utils.modbus_compat import (
    read_holding_registers,
    read_input_registers,
    read_coils,
    read_discrete_inputs,
    write_registers,
    write_register,
    write_coil,
    write_coils,
)

# serial port listing (optional)
_HAS_PYSERIAL = True
try:
    from serial.tools import list_ports
except Exception:
    _HAS_PYSERIAL = False


def _normalize_serial_port(s: str) -> str:
    if not s:
        return s
    # remove leading slashes that appear with urlparse (e.g. '/COM3')
    return s.lstrip("/")


def _expand_csv_or_range(s: Optional[str]) -> List[str]:
    """Expand a CSV string and simple ranges (e.g. '1-5') into a list of strings.

    Returns an empty list for None/empty input.
    """
    if not s:
        return []
    out: List[str] = []
    for part in str(s).split(','):
        p = part.strip()
        if not p:
            continue
        if '-' in p and p.count('-') == 1:
            a, b = p.split('-', 1)
            try:
                ia = int(a, 0)
                ib = int(b, 0)
                step = 1 if ia <= ib else -1
                for v in range(ia, ib + step, step):
                    out.append(str(v))
            except Exception:
                out.append(p)
        else:
            out.append(p)
    return out


import time
import itertools
import json

_HAS_PYMODBUS = True
ModbusTcpClient = None
ModbusSerialClient = None
try:
    # try common locations across pymodbus versions
    from pymodbus.client.sync import ModbusTcpClient, ModbusSerialClient
except Exception:
    try:
        from pymodbus.client import ModbusTcpClient, ModbusSerialClient
    except Exception:
        try:
            # fallback: some installs expose tcp/serial separately
            from pymodbus.client.sync import ModbusTcpClient
            from pymodbus.client import ModbusSerialClient
        except Exception:
            _HAS_PYMODBUS = False
from rich.console import Console
from rich.table import Table

from umdt.modbus_exceptions import get_modbus_exception_text
from umdt.core.prober import Prober, TargetSpec

app = typer.Typer()
console = Console()


def _make_controller(uri: Optional[str] = None, db_path: Optional[str] = None) -> CoreController:
    if uri:
        return CoreController(uri=uri, db_path=db_path)
    return CoreController(transport=None)



def _float_permutations_from_regs(regs: List[int]):
    # regs expected length 2
    b0 = bytes([(regs[0] >> 8) & 0xFF, regs[0] & 0xFF, (regs[1] >> 8) & 0xFF, regs[1] & 0xFF])
    ABCD = from_bytes_to_float32(b0)
    DCBA = from_bytes_to_float32(b0[::-1])
    CDAB = from_bytes_to_float32(bytes([b0[2], b0[3], b0[0], b0[1]]))
    BADC = from_bytes_to_float32(bytes([b0[1], b0[0], b0[3], b0[2]]))
    return {"Big": ABCD, "Little": DCBA, "Mid-Big": CDAB, "Mid-Little": BADC}


def _format_permutations(regs: List[int]):
    """Return structured info for the four common 32-bit orderings.

    Returns a dict mapping label -> dict with keys: `bytes`, `hex`, `uint32`, `float`.
    """
    b0 = bytes([(regs[0] >> 8) & 0xFF, regs[0] & 0xFF, (regs[1] >> 8) & 0xFF, regs[1] & 0xFF])
    perms = {
        "Big": b0,
        "Little": b0[::-1],
        "Mid-Big": bytes([b0[2], b0[3], b0[0], b0[1]]),
        "Mid-Little": bytes([b0[1], b0[0], b0[3], b0[2]]),
    }
    out = {}
    for k, bv in perms.items():
        try:
            f = from_bytes_to_float32(bv)
        except Exception:
            f = None
        u = int.from_bytes(bv, byteorder='big', signed=False)
        out[k] = {"bytes": bv, "hex": bv.hex().upper(), "uint32": u, "float": f}
    return out


def _describe_modbus_error(rr) -> str:
    """Return a concise, robust description for a Modbus response/error object.

    Handles None (timeout), pymodbus error responses and unknown objects.
    """
    if rr is None:
        return "No response (timeout)"
    try:
        # pymodbus error objects often expose isError() == True
        if hasattr(rr, 'isError') and rr.isError():
            parts = [rr.__class__.__name__]
            exc_code = None
            # try common attribute names for exception code
            for attr in ('exception_code', 'exception', 'code', 'function_code', 'error', 'status'):
                val = getattr(rr, attr, None)
                if val is not None:
                    parts.append(f"{attr}={val}")
                    if exc_code is None and attr in ('exception_code', 'exception', 'code'):
                        try:
                            exc_code = int(val)
                        except Exception:
                            exc_code = None

            # try method-style accessors (pymodbus sometimes exposes getExceptionCode)
            if exc_code is None and hasattr(rr, 'getExceptionCode'):
                try:
                    exc_code = int(rr.getExceptionCode())
                except Exception:
                    exc_code = None

            # map exception code to human text when available
            if exc_code is not None:
                text = get_modbus_exception_text(exc_code)
                if text:
                    parts.append(f"exception_text={text}")

            # include string form as fallback
            try:
                parts.append(str(rr))
            except Exception:
                parts.append(repr(rr))
            return "; ".join(parts)
    except Exception:
        pass

    # Generic fallback
    try:
        return str(rr)
    except Exception:
        return repr(rr)


def _present_long_block(start_addr: int, perms: dict, e_norm: str, address_was_hex: bool):
    """Print a table for a single long (32-bit) block starting at start_addr.

    `perms` is the dict returned by `_format_permutations` for two registers.
    """
    key_map = {'big': 'Big', 'little': 'Little', 'mid-big': 'Mid-Big', 'mid-little': 'Mid-Little'}
    if e_norm == 'all':
        display_keys = list(perms.keys())
    else:
        display_keys = [key_map.get(e_norm, 'Big')]

    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Index")
    table.add_column("Format")
    table.add_column("Hex")
    table.add_column("UInt32")
    table.add_column("Int32")
    table.add_column("Float32")
    idx_disp = hex(start_addr) if address_was_hex else str(start_addr)
    for k in display_keys:
        info = perms.get(k)
        if info is None:
            continue
        u32 = int(info.get('uint32', 0))
        i32 = u32 if u32 < 0x80000000 else u32 - 0x100000000
        table.add_row(idx_disp, k, info['hex'], str(u32), str(i32), str(info['float']))
    console.print(table)


def _present_long_table(start_addr: int, perms_list: List[dict], e_norm: str, address_was_hex: bool):
    """Present a single table containing multiple long (32-bit) value rows.

    Each item in `perms_list` is the dict returned by `_format_permutations`.
    Rows are one-per-value using the selected endian (or the single mapped key).
    """
    key_map = {'big': 'Big', 'little': 'Little', 'mid-big': 'Mid-Big', 'mid-little': 'Mid-Little'}
    # If the caller requested 'all' but only a single long value was read,
    # present all four permutations (reuse _present_long_block behavior).
    if e_norm == 'all' and len(perms_list) == 1:
        _present_long_block(start_addr, perms_list[0], e_norm, address_was_hex)
        return
    # For multi-value reads we do not support 'all' (validated earlier); pick single key
    if e_norm == 'all':
        display_key = 'Big'
    else:
        display_key = key_map.get(e_norm, 'Big')

    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Index")
    table.add_column("Format")
    table.add_column("Hex")
    table.add_column("UInt32")
    table.add_column("Int32")
    table.add_column("Float32")

    for vi, perms in enumerate(perms_list):
        idx = start_addr + (vi * 2)
        idx_disp = hex(idx) if address_was_hex else str(idx)
        info = perms.get(display_key)
        if info is None:
            continue
        u32 = int(info.get('uint32', 0))
        i32 = u32 if u32 < 0x80000000 else u32 - 0x100000000
        table.add_row(idx_disp, display_key, info['hex'], str(u32), str(i32), str(info['float']))

    console.print(table)


def _present_registers(start_addr: int, regs: List[int], e_norm: str, address_was_hex: bool):
    """Print a table for 16-bit registers starting at start_addr.

    If `e_norm` == 'all' this prints Big/Little rows for the single register.
    Otherwise prints each register with the selected endian interpretation and a Float16 column.
    """
    if e_norm == 'all':
        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Format")
        table.add_column("Hex")
        table.add_column("UInt16")
        table.add_column("Int16")
        table.add_column("Float16")
        # only one register expected when 'all' used
        r = int(regs[0])
        b = bytes([(r >> 8) & 0xFF, r & 0xFF])
        for label, bv in (('Big', b), ('Little', b[::-1])):
            uintv = int.from_bytes(bv, byteorder='big', signed=False)
            intv = uintv if uintv < 0x8000 else uintv - 0x10000
            try:
                fval = from_bytes_to_float16(bv)
            except Exception:
                fval = None
            hexv = bv.hex().upper()
            table.add_row(label, hexv, str(uintv), str(intv), str(fval))
        console.print(table)
        return

    fmt = 'big' if e_norm in ('big', 'mid-big') else 'little'
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Index")
    table.add_column("Hex")
    table.add_column("UInt16")
    table.add_column("Int16")
    table.add_column("Float16")
    for i, r in enumerate(regs):
        rr_val = int(r)
        b = bytes([(rr_val >> 8) & 0xFF, rr_val & 0xFF])
        if fmt == 'little':
            b = b[::-1]
        uintv = int.from_bytes(b, byteorder='big', signed=False)
        intv = uintv if uintv < 0x8000 else uintv - 0x10000
        try:
            fval = from_bytes_to_float16(b)
        except Exception:
            fval = None
        hexv = '0x' + b.hex().upper()
        idx = start_addr + i
        idx_disp = hex(idx) if address_was_hex else str(idx)
        table.add_row(idx_disp, hexv, str(uintv), str(intv), str(fval))
    console.print(table)


def _present_bits(start_addr: int, bits: List[bool], address_was_hex: bool, label: str):
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Index")
    table.add_column(label)
    table.add_column("State")
    table.add_column("Byte Offset")
    table.add_column("Bit")
    for i, bit in enumerate(bits):
        idx = start_addr + i
        idx_disp = hex(idx) if address_was_hex else str(idx)
        state = "ON" if bit else "OFF"
        table.add_row(
            idx_disp,
            "1" if bit else "0",
            state,
            str(i // 8),
            str(i % 8),
        )
    console.print(table)


def _extract_values(rr, bit_based: bool):
    if rr is None:
        return None, "No response (timeout)"
    if hasattr(rr, 'isError') and rr.isError():
        return None, _describe_modbus_error(rr)

    attr = 'bits' if bit_based else 'registers'
    values = getattr(rr, attr, None)
    if values is not None:
        return list(values), None

    try:
        return list(rr), None
    except Exception:
        return [rr], None


@app.command()
def read(
    serial: Optional[str] = typer.Option(None, help="Serial port (e.g. COM5) to read from"),
    baud: int = typer.Option(9600, help="Baud rate for serial"),
    host: Optional[str] = typer.Option(None, help="Modbus TCP host"),
    port: int = typer.Option(502, help="Modbus TCP port"),
    unit: int = typer.Option(1, help="Modbus unit id"),
    address: Optional[str] = typer.Option(None, prompt="Starting address (decimal or 0xHEX)", help="Starting address (decimal or 0xHEX)"),
    count: int = typer.Option(1, help="Number of values to read (registers, coils, or inputs)"),
    long: bool = typer.Option(False, "--long", "-l", help="Read 32-bit values (two registers per value); register types only"),
    endian: str = typer.Option("big", "--endian", "-e", help="Endian to use for register types: big|little|mid-big|mid-little|all"),
    datatype: str = typer.Option("holding", "--datatype", "-d", help="Data type: holding|input|coil|discrete"),
):
    """Decode registers locally, or read from a device via `--serial/--baud` or `--host/--port`.

    By default a single 16-bit register is read. Use `--long` (`-l`) to read two registers
    and show float permutations (concatenation of two registers).
    Examples:
      - Local decode: `umdt read --regs 0x4120,0x0000`
      - Serial read: `umdt read --serial COM5 --baud 115200 --unit 1 --address 1 -l`
      - TCP read: `umdt read --host 192.168.1.10 --port 502 --unit 1 --address 1 -l`
    """
    # Validate mutually exclusive connection flags
    conn_methods = sum(bool(x) for x in (serial, host))
    if conn_methods > 1:
        console.print("Specify only one of --serial or --host")
        raise typer.Exit(code=1)

    # If connection method not provided, prompt the user (wizard-style)
    if not (serial or host):
        method = typer.prompt("Connect via 'serial' or 'tcp'?", default="serial")
        method = (method or "").strip().lower()
        if method.startswith('s'):
            serial = typer.prompt("Serial port (e.g. COM5)")
            try:
                baud = int(typer.prompt("Baud rate", default=str(baud)))
            except Exception:
                baud = baud
        else:
            host = typer.prompt("Modbus TCP host (IP or hostname)")
            try:
                port = int(typer.prompt("Modbus TCP port", default=str(port)))
            except Exception:
                port = port

    try:
        data_type = parse_data_type(datatype)
    except ValueError as exc:
        console.print(str(exc))
        raise typer.Exit(code=1)

    props = DATA_TYPE_PROPERTIES[data_type]
    if not props.readable or not props.pymodbus_read_method:
        console.print(f"Data type '{data_type.value}' is not readable")
        raise typer.Exit(code=1)

    if long and not is_register_type(data_type):
        console.print("--long is only valid for holding/input registers")
        raise typer.Exit(code=1)

    # proceed with provided or prompted connection info
    if serial or host:
        if not _HAS_PYMODBUS:
            console.print("pymodbus is required to read from a device")
            raise typer.Exit(code=1)

        client = None
        if serial:
            sp = _normalize_serial_port(serial)
            if 'ModbusSerialClient' in globals() and ModbusSerialClient is not None:
                client = ModbusSerialClient(port=sp, baudrate=baud)
            else:
                client = create_client(kind='serial', serial_port=sp, baudrate=baud)
        else:
            if 'ModbusTcpClient' in globals() and ModbusTcpClient is not None:
                client = ModbusTcpClient(host, port=port)
            else:
                client = create_client(kind='tcp', host=host, port=port)

        if not getattr(client, 'connect', lambda: True)():
            console.print("Failed to connect")
            raise typer.Exit(code=1)

        try:
            # parse address (support decimal or 0xHEX input)
            address_was_hex = False
            if isinstance(address, str):
                a_str = address.strip()
                if a_str.lower().startswith("0x"):
                    address_was_hex = True
                try:
                    numeric_address = int(a_str, 0)
                except Exception:
                    console.print("Invalid address format")
                    raise typer.Exit(code=1)
            else:
                numeric_address = int(address)
                address_was_hex = False

            num_values = max(1, int(count))
            if is_register_type(data_type):
                regs_to_read = num_values * (2 if long else 1)
                if regs_to_read > 125:
                    console.print(f"Requested {regs_to_read} registers exceeds Modbus limit of 125")
                    close_client(client)
                    raise typer.Exit(code=1)
            else:
                regs_to_read = num_values
                if regs_to_read > 2000:
                    console.print("Requested coil/input count exceeds Modbus limit of 2000")
                    close_client(client)
                    raise typer.Exit(code=1)

            # normalize endian option
            e_str = (endian or "big").lower()
            _endian_map = {
                'b': 'big', 'big': 'big',
                'l': 'little', 'little': 'little',
                'mb': 'mid-big', 'mid-big': 'mid-big',
                'ml': 'mid-little', 'mid-little': 'mid-little',
                'all': 'all'
            }
            e_norm = _endian_map.get(e_str, 'big')
            if not is_register_type(data_type):
                if e_norm != 'big' and endian:
                    console.print("Ignoring --endian for coil/discrete types")
                e_norm = 'big'
            elif e_norm == 'all' and num_values > 1:
                console.print("--endian all cannot be used with --count greater than 1")
                client.close()
                raise typer.Exit(code=1)

            # Perform read using compat wrappers (or fallback to client method)
            try:
                _read_map = {
                    'read_holding_registers': read_holding_registers,
                    'read_input_registers': read_input_registers,
                    'read_coils': read_coils,
                    'read_discrete_inputs': read_discrete_inputs,
                }
                reader = _read_map.get(props.pymodbus_read_method)
                if reader:
                    rr = reader(client, numeric_address, regs_to_read, unit)
                else:
                    fn = getattr(client, props.pymodbus_read_method, None)
                    if fn is None:
                        raise AttributeError(f"Client does not support {props.pymodbus_read_method}")
                    rr = fn(numeric_address, regs_to_read, unit)
            except AttributeError as exc:
                console.print(str(exc))
                close_client(client)
                raise typer.Exit(code=1)

            values, err = _extract_values(rr, is_bit_type(data_type))
            if err:
                console.print(f"[red]Read error: {err}[/red]")
            else:
                if is_register_type(data_type):
                    regs = [int(v) & 0xFFFF for v in values]
                    if long:
                        perms_list = []
                        for vi in range(num_values):
                            base_idx = vi * 2
                            if base_idx + 1 >= len(regs):
                                console.print(f"Not enough registers for long value {vi}")
                                break
                            a = regs[base_idx]
                            b = regs[base_idx + 1]
                            perms = _format_permutations([a, b])
                            perms_list.append(perms)
                        _present_long_table(numeric_address, perms_list, e_norm, address_was_hex)
                    else:
                        _present_registers(numeric_address, regs, e_norm, address_was_hex)
                else:
                    bit_label = "Coil" if data_type == DataType.COIL else "Input"
                    _present_bits(numeric_address, [bool(v) for v in values], address_was_hex, bit_label)
        finally:
            close_client(client)
        return

    # Local decode removed — use `decode` command for offline decoding


@app.command()
def decode(values: List[str] = typer.Argument(..., help="One or two register values (decimal or 0xHEX), e.g. '1' or '0x4120 0x0000'")):
    """Decode one 16-bit register or a pair of 16-bit registers as a 32-bit value.

    - If one value is provided it is treated as a single 16-bit register and printed
      with `--all` semantics (Big/Little rows with Hex/UInt/Int/Float16).
    - If two values are provided they are treated as two consecutive 16-bit registers
      (high-word first) and decoded as a single 32-bit value with `--all --long`
      semantics (Big/Little/Mid-Big/Mid-Little permutations shown).
    """
    if not values or len(values) not in (1, 2):
        console.print("Provide one or two register values (decimal or 0xHEX)")
        raise typer.Exit(code=1)

    try:
        regs_int = [int(v, 0) for v in values]
    except Exception:
        console.print("Invalid register value; use decimal or 0xHEX format")
        raise typer.Exit(code=1)

    if len(regs_int) == 1:
        # single 16-bit register: show Big/Little rows (Float16 included)
        _present_registers(0, regs_int, 'all', False)
        return

    # two 16-bit registers: present 32-bit permutations
    perms = _format_permutations(regs_int)
    _present_long_block(0, perms, 'all', False)



@app.command()
def monitor(
    serial: Optional[str] = typer.Option(None, help="Serial port (e.g. COM5) to monitor"),
    baud: int = typer.Option(9600, help="Baud rate for serial"),
    host: Optional[str] = typer.Option(None, help="Modbus TCP host"),
    port: int = typer.Option(502, help="Modbus TCP port"),
    unit: int = typer.Option(1, help="Modbus unit id"),
    address: Optional[str] = typer.Option(None, prompt="Starting address (decimal or 0xHEX)", help="Starting address (decimal or 0xHEX)"),
    count: int = typer.Option(1, help="Number of values per poll"),
    long: bool = typer.Option(False, "--long", "-l", help="Read 32-bit values (two registers per value); register types only"),
    endian: str = typer.Option("big", "--endian", "-e", help="Endian for register types: big|little|mid-big|mid-little|all"),
    datatype: str = typer.Option("holding", "--datatype", "-d", help="Data type: holding|input|coil|discrete"),
    interval: float = typer.Option(1.0, help="Poll interval seconds"),
):
    """Continuously poll registers and display values using the same formatting as `read`.

    Behaves like `read` but polls repeatedly. Use `--serial`/`--baud` or `--host`/`--port`,
    `--unit`, `--address` (supports 0xHEX), `--count`, and `--long` the same way as `read`.
    """
    if not _HAS_PYMODBUS:
        console.print("pymodbus is required for monitor")
        raise typer.Exit(code=1)

    # Validate mutually exclusive connection flags
    conn_methods = sum(bool(x) for x in (serial, host))
    if conn_methods > 1:
        console.print("Specify only one of --serial or --host")
        raise typer.Exit(code=1)

    # If connection method not provided, prompt the user (wizard-style)
    if not (serial or host):
        method = typer.prompt("Connect via 'serial' or 'tcp'?", default="serial")
        method = (method or "").strip().lower()
        if method.startswith('s'):
            serial = typer.prompt("Serial port (e.g. COM5)")
            try:
                baud = int(typer.prompt("Baud rate", default=str(baud)))
            except Exception:
                baud = baud
        else:
            host = typer.prompt("Modbus TCP host (IP or hostname)")
            try:
                port = int(typer.prompt("Modbus TCP port", default=str(port)))
            except Exception:
                port = port

    try:
        data_type = parse_data_type(datatype)
    except ValueError as exc:
        console.print(str(exc))
        raise typer.Exit(code=1)

    props = DATA_TYPE_PROPERTIES[data_type]
    if not props.readable or not props.pymodbus_read_method:
        console.print(f"Data type '{data_type.value}' is not readable")
        raise typer.Exit(code=1)

    if long and not is_register_type(data_type):
        console.print("--long is only valid for holding/input registers")
        raise typer.Exit(code=1)

    client = None
    if serial:
        sp = _normalize_serial_port(serial)
        if 'ModbusSerialClient' in globals() and ModbusSerialClient is not None:
            client = ModbusSerialClient(port=sp, baudrate=baud)
        else:
            client = create_client(kind='serial', serial_port=sp, baudrate=baud)
    else:
        if 'ModbusTcpClient' in globals() and ModbusTcpClient is not None:
            client = ModbusTcpClient(host, port=port)
        else:
            client = create_client(kind='tcp', host=host, port=port)

    if not getattr(client, 'connect', lambda: True)():
        console.print("Failed to connect")
        raise typer.Exit(code=1)

    console.print("Monitoring... Ctrl-C to stop")
    try:
        # parse starting address once
        address_was_hex = False
        a_str = address.strip() if isinstance(address, str) else str(address)
        if a_str.lower().startswith("0x"):
            address_was_hex = True
        try:
            numeric_address = int(a_str, 0)
        except Exception:
            console.print("Invalid address format")
            raise typer.Exit(code=1)

        num_values = max(1, int(count))
        if is_register_type(data_type):
            regs_to_read = num_values * (2 if long else 1)
            if regs_to_read > 125:
                console.print(f"Requested {regs_to_read} registers exceeds Modbus limit of 125")
                client.close()
                raise typer.Exit(code=1)
        else:
            regs_to_read = num_values
            if regs_to_read > 2000:
                console.print("Requested coil/input count exceeds Modbus limit of 2000")
                client.close()
                raise typer.Exit(code=1)

        e_str = (endian or "big").lower()
        _endian_map = {
            'b': 'big', 'big': 'big',
            'l': 'little', 'little': 'little',
            'mb': 'mid-big', 'mid-big': 'mid-big',
            'ml': 'mid-little', 'mid-little': 'mid-little',
            'all': 'all'
        }
        e_norm = _endian_map.get(e_str, 'big')
        if not is_register_type(data_type):
            if e_norm != 'big' and endian:
                console.print("Ignoring --endian for coil/discrete types")
            e_norm = 'big'
        elif e_norm == 'all' and num_values > 1:
            console.print("--endian all cannot be used with --count greater than 1")
            client.close()
            raise typer.Exit(code=1)

        while True:
            try:
                _read_map = {
                    'read_holding_registers': read_holding_registers,
                    'read_input_registers': read_input_registers,
                    'read_coils': read_coils,
                    'read_discrete_inputs': read_discrete_inputs,
                }
                reader = _read_map.get(props.pymodbus_read_method)
                if reader:
                    rr = reader(client, numeric_address, regs_to_read, unit)
                else:
                    fn = getattr(client, props.pymodbus_read_method, None)
                    if fn is None:
                        raise AttributeError(f"Client does not support {props.pymodbus_read_method}")
                    rr = fn(numeric_address, regs_to_read, unit)
            except AttributeError as exc:
                console.print(str(exc))
                break

            values, err = _extract_values(rr, is_bit_type(data_type))
            if err:
                console.print(f"[red]Read error: {err}[/red]")
            else:
                if is_register_type(data_type):
                    regs = [int(v) & 0xFFFF for v in values]
                    if long:
                        perms_list = []
                        for vi in range(num_values):
                            base_idx = vi * 2
                            if base_idx + 1 >= len(regs):
                                console.print(f"Not enough registers for long value {vi}")
                                break
                            perms_list.append(_format_permutations([regs[base_idx], regs[base_idx + 1]]))
                        _present_long_table(numeric_address, perms_list, e_norm, address_was_hex)
                    else:
                        _present_registers(numeric_address, regs, e_norm, address_was_hex)
                else:
                    bit_label = "Coil" if data_type == DataType.COIL else "Input"
                    _present_bits(numeric_address, [bool(v) for v in values], address_was_hex, bit_label)

            time.sleep(interval)
    except KeyboardInterrupt:
        console.print("Stopping monitor...")
    finally:
        close_client(client)



@app.command()
def ports():
    """List available serial ports (requires `pyserial`)."""
    if not _HAS_PYSERIAL:
        console.print("pyserial not installed; cannot list ports")
        raise typer.Exit(code=1)
    found = list_ports.comports()
    if not found:
        console.print("No serial ports found")
        raise typer.Exit()
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Device")
    table.add_column("Description")
    for p in found:
        table.add_row(p.device, p.description or "")
    console.print(table)


@app.command()
def scan(
    start: str = typer.Argument(..., help="Start address (decimal or 0xHEX)"),
    end: str = typer.Argument(..., help="End address (decimal or 0xHEX)"),
    serial: Optional[str] = typer.Option(None, help="Serial port (e.g. COM5) to scan"),
    baud: int = typer.Option(9600, help="Baud rate for serial"),
    host: Optional[str] = typer.Option(None, help="Modbus TCP host"),
    port: int = typer.Option(502, help="Modbus TCP port"),
    unit: int = typer.Option(1, help="Modbus unit id"),
    datatype: str = typer.Option("holding", "--datatype", "-d", help="Data type: holding|input|coil|discrete"),
):
    """Scan a range of Modbus addresses to find readable registers.

    Attempts to read each address in the range as a single register/coil.
    Only prints addresses that return successful reads (errors are silently ignored).

    Examples:
      - Scan holding registers: `umdt scan 0 100 --host 192.168.1.10`
      - Scan serial coils: `umdt scan 0x0000 0x00FF --serial COM5 --datatype coil`
    """
    # Validate connection method
    conn_methods = sum(bool(x) for x in (serial, host))
    if conn_methods != 1:
        console.print("Specify exactly one of --serial or --host")
        raise typer.Exit(code=1)

    if not _HAS_PYMODBUS:
        console.print("pymodbus is required for scanning")
        raise typer.Exit(code=1)

    # Parse data type
    try:
        data_type = parse_data_type(datatype)
    except ValueError as exc:
        console.print(str(exc))
        raise typer.Exit(code=1)

    props = DATA_TYPE_PROPERTIES[data_type]
    if not props.readable or not props.pymodbus_read_method:
        console.print(f"Data type '{data_type.value}' is not readable")
        raise typer.Exit(code=1)

    # Parse start/end addresses and detect if hex was used
    start_was_hex = start.strip().lower().startswith("0x")
    end_was_hex = end.strip().lower().startswith("0x")
    
    try:
        start_addr = int(start, 0)
        end_addr = int(end, 0)
    except Exception:
        console.print("Invalid address format; use decimal or 0xHEX")
        raise typer.Exit(code=1)

    if start_addr > end_addr:
        console.print("Start address must be <= end address")
        raise typer.Exit(code=1)

    # Determine output format (use hex if either bound was specified in hex)
    use_hex = start_was_hex or end_was_hex

    # Create client
    if serial:
        sp = _normalize_serial_port(serial)
        if 'ModbusSerialClient' in globals() and ModbusSerialClient is not None:
            client = ModbusSerialClient(port=sp, baudrate=baud)
        else:
            client = create_client(kind='serial', serial_port=sp, baudrate=baud)
    else:
        if 'ModbusTcpClient' in globals() and ModbusTcpClient is not None:
            client = ModbusTcpClient(host, port=port)
        else:
            client = create_client(kind='tcp', host=host, port=port)

    if not getattr(client, 'connect', lambda: True)():
        console.print("Failed to connect")
        raise typer.Exit(code=1)

    try:
        console.print(f"Scanning {start_addr} to {end_addr} ({end_addr - start_addr + 1} addresses)...")
        successful = []
        
        for addr in range(start_addr, end_addr + 1):
            try:
                # Read single register/coil at this address
                _read_map = {
                    'read_holding_registers': read_holding_registers,
                    'read_input_registers': read_input_registers,
                    'read_coils': read_coils,
                    'read_discrete_inputs': read_discrete_inputs,
                }
                reader = _read_map.get(props.pymodbus_read_method)
                if reader:
                    rr = reader(client, addr, 1, unit)
                else:
                    rr = call_read_method(client, props.pymodbus_read_method, addr, 1, unit)
                
                # Check if successful
                if rr is not None and not (hasattr(rr, 'isError') and rr.isError()):
                    successful.append(addr)
                    # Print immediately for live feedback
                    if use_hex:
                        console.print(f"  {hex(addr)}")
                    else:
                        console.print(f"  {addr}")
            except Exception:
                # Silently ignore errors
                pass

        console.print(f"\nScan complete. Found {len(successful)} readable address(es).")
        
    finally:
        close_client(client)


@app.command()
def write(
    serial: Optional[str] = typer.Option(None, help="Serial port (e.g. COM5) to write to"),
    baud: int = typer.Option(9600, help="Baud rate for serial"),
    host: Optional[str] = typer.Option(None, help="Modbus TCP host"),
    port: int = typer.Option(502, help="Modbus TCP port"),
    unit: int = typer.Option(1, help="Modbus unit id"),
    address: Optional[str] = typer.Option(None, prompt="Starting address (decimal or 0xHEX)", help="Starting address (decimal or 0xHEX)"),
    long: bool = typer.Option(False, "--long", "-l", help="Write a 32-bit (two-register) value; register types only"),
    endian: str = typer.Option("big", "--endian", "-e", help="Endian for register types: big|little|mid-big|mid-little"),
    float_mode: bool = typer.Option(False, "--float", help="Interpret the value as a float (16-bit by default, 32-bit with --long)"),
    signed: bool = typer.Option(False, "--signed", help="Interpret integer value as signed and validate against signed range"),
    datatype: str = typer.Option("holding", "--datatype", "-d", help="Data type: holding|coil"),
    value: Optional[str] = typer.Argument(None, help="Value to write; registers accept int/float, coils accept comma-separated booleans"),
):
    """Write register or coil values using the selected transport."""

    if not _HAS_PYMODBUS:
        console.print("pymodbus is required for write")
        raise typer.Exit(code=1)

    try:
        data_type = parse_data_type(datatype)
    except ValueError as exc:
        console.print(str(exc))
        raise typer.Exit(code=1)

    props = DATA_TYPE_PROPERTIES.get(data_type)
    if not props or not props.writable or not props.pymodbus_write_method:
        console.print(f"Data type '{data_type.value}' is not writable")
        raise typer.Exit(code=1)

    if not is_register_type(data_type):
        if long:
            console.print("--long is only valid for holding registers")
            raise typer.Exit(code=1)
        if float_mode:
            console.print("--float is not supported for coil writes")
            raise typer.Exit(code=1)
        if signed:
            console.print("--signed is not applicable for coil writes")
            raise typer.Exit(code=1)

    conn_methods = sum(bool(x) for x in (serial, host))
    if conn_methods > 1:
        console.print("Specify only one of --serial or --host")
        raise typer.Exit(code=1)

    if not (serial or host):
        method = typer.prompt("Connect via 'serial' or 'tcp'?", default="serial")
        method = (method or "").strip().lower()
        if method.startswith("s"):
            serial = typer.prompt("Serial port (e.g. COM5)")
            try:
                baud = int(typer.prompt("Baud rate", default=str(baud)))
            except Exception:
                baud = baud
        else:
            host = typer.prompt("Modbus TCP host (IP or hostname)")
            try:
                port = int(typer.prompt("Modbus TCP port", default=str(port)))
            except Exception:
                port = port

    if address is None:
        address = typer.prompt("Starting address (decimal or 0xHEX)")
    address_was_hex = False
    a_str = address.strip() if isinstance(address, str) else str(address)
    if a_str.lower().startswith("0x"):
        address_was_hex = True
    try:
        numeric_address = int(a_str, 0)
    except Exception:
        console.print("Invalid address format")
        raise typer.Exit(code=1)

    e_str = (endian or "big").lower()
    endian_map = {
        "b": "big", "big": "big",
        "l": "little", "little": "little",
        "mb": "mid-big", "mid-big": "mid-big",
        "ml": "mid-little", "mid-little": "mid-little",
    }
    e_norm = endian_map.get(e_str, "big")
    if not is_register_type(data_type):
        if endian and e_norm != "big":
            console.print("Ignoring --endian for coil writes")
        e_norm = "big"
    elif e_norm is None:
        console.print(f"Unknown endian '{endian}'")
        raise typer.Exit(code=1)

    if value is None:
        if not is_register_type(data_type):
            value = typer.prompt("Value(s) to write (comma separated 0/1/true/false)")
        elif float_mode:
            value = typer.prompt("Value to write (float)")
        else:
            value = typer.prompt("Value to write (integer or 0xHEX)")
        if value is None or str(value).strip() == "":
            console.print("No value provided")
            raise typer.Exit(code=1)

    payload_values: List = []
    if not is_register_type(data_type):
        tokens = [part for part in str(value).replace(',', ' ').split() if part]
        if not tokens:
            console.print("Provide at least one coil value (0/1/true/false)")
            raise typer.Exit(code=1)
        for token in tokens:
            tl = token.strip().lower()
            if tl in ("1", "true", "on", "set"):
                payload_values.append(True)
            elif tl in ("0", "false", "off", "clear"):
                payload_values.append(False)
            else:
                console.print(f"Unknown coil value '{token}' (use 0/1/true/false)")
                raise typer.Exit(code=1)
    else:
        is_hex = isinstance(value, str) and value.strip().lower().startswith("0x")
        if float_mode:
            if is_hex:
                console.print("Hex values are not allowed when using --float")
                raise typer.Exit(code=1)
            try:
                float_val = float(value)
            except Exception:
                console.print("Value must be a float (or integer) when using --float")
                raise typer.Exit(code=1)
        else:
            try:
                int_val = int(value, 0)
            except Exception:
                console.print("Value must be an integer or 0xHEX when not using --float")
                raise typer.Exit(code=1)

            if int_val < 0:
                signed = True

            if long:
                if signed:
                    if int_val < -0x80000000 or int_val > 0x7FFFFFFF:
                        console.print("Value out of 32-bit signed range (-2^31..2^31-1)")
                        raise typer.Exit(code=1)
                else:
                    if int_val < 0 or int_val > 0xFFFFFFFF:
                        console.print("Value out of 32-bit unsigned range (0..2^32-1)")
                        raise typer.Exit(code=1)
            else:
                if signed:
                    if int_val < -0x8000 or int_val > 0x7FFF:
                        console.print("Value out of 16-bit signed range (-2^15..2^15-1)")
                        raise typer.Exit(code=1)
                else:
                    if int_val < 0 or int_val > 0xFFFF:
                        console.print("Value out of 16-bit unsigned range (0..2^16-1)")
                        raise typer.Exit(code=1)

        if float_mode:
            import struct

            if long:
                raw_be = struct.pack("!f", float_val)
                if e_norm == "big":
                    bv = raw_be
                elif e_norm == "little":
                    bv = raw_be[::-1]
                elif e_norm == "mid-big":
                    bv = bytes([raw_be[2], raw_be[3], raw_be[0], raw_be[1]])
                else:
                    bv = bytes([raw_be[1], raw_be[0], raw_be[3], raw_be[2]])
                payload_values = [
                    int.from_bytes(bv[0:2], byteorder="big"),
                    int.from_bytes(bv[2:4], byteorder="big"),
                ]
            else:
                import math

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
                if e_norm in ("little",):
                    b = b[::-1]
                payload_values = [int.from_bytes(b, byteorder="big")]
        else:
            width_bits = 32 if long else 16
            max_val = 1 << width_bits
            int_u = int_val & (max_val - 1) if signed else int_val
            byte_len = width_bits // 8
            bv = int_u.to_bytes(byte_len, byteorder="big", signed=False)

            if long:
                if e_norm == "little":
                    bv = bv[::-1]
                elif e_norm == "mid-big":
                    bv = bytes([bv[2], bv[3], bv[0], bv[1]])
                elif e_norm == "mid-little":
                    bv = bytes([bv[1], bv[0], bv[3], bv[2]])
                payload_values = [
                    int.from_bytes(bv[0:2], byteorder="big"),
                    int.from_bytes(bv[2:4], byteorder="big"),
                ]
            else:
                if e_norm in ("little",):
                    bv = bv[::-1]
                payload_values = [int.from_bytes(bv, byteorder="big")]

    try:
        table = Table(show_header=True, header_style="bold magenta")
        if is_register_type(data_type):
            table.add_column("Index")
            table.add_column("Hex")
            table.add_column("Value")
            for i, r in enumerate(payload_values):
                idx = numeric_address + i
                idx_disp = hex(idx) if address_was_hex else str(idx)
                hexv = '0x' + int(r).to_bytes(2, byteorder='big').hex().upper()
                table.add_row(idx_disp, hexv, str(int(r)))
        else:
            table.add_column("Index")
            table.add_column("Value")
            table.add_column("State")
            for i, bit in enumerate(payload_values):
                idx = numeric_address + i
                idx_disp = hex(idx) if address_was_hex else str(idx)
                table.add_row(idx_disp, "1" if bit else "0", "ON" if bit else "OFF")
        console.print(table)
    except Exception:
        pass

    if serial:
        sp = _normalize_serial_port(serial)
        if 'ModbusSerialClient' in globals() and ModbusSerialClient is not None:
            client = ModbusSerialClient(port=sp, baudrate=baud)
        else:
            client = create_client(kind='serial', serial_port=sp, baudrate=baud)
    else:
        if 'ModbusTcpClient' in globals() and ModbusTcpClient is not None:
            client = ModbusTcpClient(host, port=port)
        else:
            client = create_client(kind='tcp', host=host, port=port)

    if not getattr(client, 'connect', lambda: True)():
        console.print("Failed to connect")
        raise typer.Exit(code=1)

    try:
        # Prefer register-specific wrappers when available
        if is_register_type(data_type):
            if props.pymodbus_write_method == 'write_registers':
                res = write_registers(client, numeric_address, payload_values, unit)
            elif props.pymodbus_write_method == 'write_register':
                val = payload_values[0] if isinstance(payload_values, (list, tuple)) else payload_values
                res = write_register(client, numeric_address, val, unit)
            else:
                fn = getattr(client, props.pymodbus_write_method, None)
                if fn is None:
                    raise AttributeError(f"Client does not support {props.pymodbus_write_method}")
                res = fn(numeric_address, payload_values, unit)
        else:
            # Coils: prefer wrappers
            if props.pymodbus_write_method == 'write_coil':
                res = write_coil(client, numeric_address, payload_values[0] if isinstance(payload_values, (list, tuple)) else payload_values, unit)
            elif props.pymodbus_write_method == 'write_coils':
                res = write_coils(client, numeric_address, payload_values, unit)
            else:
                fn = getattr(client, props.pymodbus_write_method, None)
                if fn is None:
                    raise AttributeError(f"Client does not support {props.pymodbus_write_method}")
                res = fn(numeric_address, payload_values, unit)
    except AttributeError as exc:
        console.print(str(exc))
        raise typer.Exit(code=1)

        if hasattr(res, "isError") and res.isError():
            console.print("[red]Write failed[/red]")
        else:
            console.print("[green]Write OK[/green]")
    finally:
        close_client(client)




@app.command()
def probe(
    hosts: Optional[str] = typer.Option(None, help="Comma-separated hosts or range (e.g. '192.168.1.1-10')"),
    ports: Optional[str] = typer.Option(None, help="Comma-separated ports or range (e.g. '502,503' or '500-510')"),
    serials: Optional[str] = typer.Option(None, help="Comma-separated serial ports (e.g. 'COM5,COM6')"),
    bauds: Optional[str] = typer.Option(None, help="Comma-separated baud rates or range (e.g. '9600,115200')"),
    units: str = typer.Option("1", help="Comma-separated unit IDs or range (e.g. '1-5')"),
    address: str = typer.Option("0", help="Target register address (decimal or 0xHEX)"),
    datatype: str = typer.Option("holding", "--datatype", "-d", help="Data type: holding|input|coil|discrete"),
    timeout: int = typer.Option(100, help="Timeout in milliseconds per probe"),
    concurrency: int = typer.Option(32, help="Maximum concurrent probes"),
    attempts: int = typer.Option(1, help="Number of attempts per probe"),
    backoff: int = typer.Option(0, help="Backoff in milliseconds between attempts"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Output results to JSON file"),
    alive_only: bool = typer.Option(False, "--alive-only", help="Only show alive results"),
):
    """Probe Modbus endpoints to discover working connections.

    Tests combinations of connection parameters (TCP hosts/ports or serial ports/bauds)
    against a target register to identify responding devices.

    Examples:
      - Probe TCP ports: `umdt probe --hosts 127.0.0.1 --ports 500-550`
      - Probe serial: `umdt probe --serials COM5,COM6 --bauds 9600,115200`
      - Combined: `umdt probe --hosts 192.168.1.10 --ports 502 --units 1-10`
    """
    if not _HAS_PYMODBUS:
        console.print("pymodbus is required for probing")
        raise typer.Exit(code=1)

    # Build combinations
    combinations = []
    
    # Expand parameters
    host_list = _expand_csv_or_range(hosts) if hosts else []
    port_list = _expand_csv_or_range(ports) if ports else []
    serial_list = _expand_csv_or_range(serials) if serials else []
    baud_list = _expand_csv_or_range(bauds) if bauds else []
    unit_list = _expand_csv_or_range(units) if units else ["1"]
    
    # Build TCP combinations
    if host_list and port_list:
        for h in host_list:
            for p in port_list:
                for u in unit_list:
                    try:
                        combinations.append({"host": h, "port": int(p, 0), "unit": int(u, 0)})
                    except Exception:
                        console.print(f"[yellow]Warning: skipping invalid TCP combo {h}:{p} unit {u}[/yellow]")
    
    # Build serial combinations
    if serial_list:
        if not baud_list:
            baud_list = ["9600"]
        for dev in serial_list:
            for bd in baud_list:
                for u in unit_list:
                    try:
                        combinations.append({"serial": dev, "baud": int(bd, 0), "unit": int(u, 0)})
                    except Exception:
                        console.print(f"[yellow]Warning: skipping invalid serial combo {dev}@{bd} unit {u}[/yellow]")
    
    if not combinations:
        console.print("[red]No valid combinations to probe. Specify --hosts/--ports or --serials/--bauds.[/red]")
        raise typer.Exit(code=1)
    
    # Parse target address and datatype
    try:
        numeric_address = int(address, 0)
    except Exception:
        console.print("[red]Invalid address format[/red]")
        raise typer.Exit(code=1)
    
    try:
        data_type = parse_data_type(datatype)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    
    target = TargetSpec(datatype=data_type, address=numeric_address)
    
    # Create prober
    prober = Prober(
        timeout_ms=timeout,
        concurrency=concurrency,
        attempts=attempts,
        backoff_ms=backoff
    )
    
    console.print(f"Probing {len(combinations)} combination(s)...")
    console.print(f"  Target: {data_type.value} register at address {address}")
    console.print(f"  Timeout: {timeout}ms, Concurrency: {concurrency}, Attempts: {attempts}")
    
    # Run probe
    results = []
    
    def on_result(pr):
        results.append(pr)
        # Live feedback for alive results only
        if pr.alive:
            if not alive_only:
                console.print(f"[green]✓[/green] {pr.uri} - {pr.response_summary} ({pr.elapsed_ms:.1f}ms)")
            else:
                console.print(f"[green]✓[/green] {pr.uri}")
    
    async def run_probe():
        return await prober.run(combinations, target, on_result=on_result)
    
    try:
        asyncio.run(run_probe())
    except KeyboardInterrupt:
        console.print("\n[yellow]Probe cancelled[/yellow]")
        raise typer.Exit(code=1)
    
    # Summary
    alive_count = sum(1 for r in results if r.alive)
    console.print(f"\nProbe complete:")
    console.print(f"  Tested: {len(results)} / {len(combinations)}")
    console.print(f"  Alive: {alive_count}")
    console.print(f"  Dead: {len(results) - alive_count}")
    
    # Display results table (alive results only)
    if alive_count > 0:
        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("URI")
        table.add_column("Summary")
        table.add_column("Time (ms)")
        
        for pr in results:
            if pr.alive:
                table.add_row(pr.uri, pr.response_summary or "", f"{pr.elapsed_ms:.1f}")
        
        console.print(table)
    elif len(results) > 0:
        console.print("[yellow]No alive results found[/yellow]")
    
    # Export to JSON if requested
    if output:
        import json
        try:
            export_data = [
                {
                    "uri": pr.uri,
                    "params": pr.params,
                    "alive": pr.alive,
                    "response_summary": pr.response_summary,
                    "elapsed_ms": pr.elapsed_ms
                }
                for pr in results
            ]
            with open(output, 'w') as f:
                json.dump(export_data, f, indent=2)
            console.print(f"[green]Results exported to {output}[/green]")
        except Exception as e:
            console.print(f"[red]Failed to export results: {e}[/red]")


if __name__ == "__main__":
    app()
