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
from umdt.utils.modbus_compat import call_read_method, call_write_method

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


import time

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
            client = ModbusSerialClient(port=sp, baudrate=baud)
        else:
            client = ModbusTcpClient(host, port=port)

        if not client.connect():
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
                    client.close()
                    raise typer.Exit(code=1)
            else:
                regs_to_read = num_values
                if regs_to_read > 2000:
                    console.print("Requested coil/input count exceeds Modbus limit of 2000")
                    client.close()
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

            try:
                rr = call_read_method(client, props.pymodbus_read_method, numeric_address, regs_to_read, unit)
            except AttributeError as exc:
                console.print(str(exc))
                client.close()
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
            client.close()
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
        client = ModbusSerialClient(port=sp, baudrate=baud)
    else:
        client = ModbusTcpClient(host, port=port)

    if not client.connect():
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
                rr = call_read_method(client, props.pymodbus_read_method, numeric_address, regs_to_read, unit)
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
        client.close()



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
        client = ModbusSerialClient(port=sp, baudrate=baud)
    else:
        client = ModbusTcpClient(host, port=port)

    if not client.connect():
        console.print("Failed to connect")
        raise typer.Exit(code=1)

    try:
        try:
            res = call_write_method(client, props.pymodbus_write_method, numeric_address, payload_values, unit)
        except AttributeError as exc:
            console.print(str(exc))
            raise typer.Exit(code=1)

        if hasattr(res, "isError") and res.isError():
            console.print("[red]Write failed[/red]")
        else:
            console.print("[green]Write OK[/green]")
    finally:
        client.close()




if __name__ == "__main__":
    app()
