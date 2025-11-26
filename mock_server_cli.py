from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from umdt.core.data_types import DataType, parse_data_type
from umdt.mock_server import MockDevice, TransportCoordinator, load_config

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    yaml = None

app = typer.Typer(help="Diagnostics mock Modbus server CLI")
groups_app = typer.Typer(help="Manage register groups in config files")
values_app = typer.Typer(help="Manage register-level rules")
faults_app = typer.Typer(help="Manage fault injection defaults")
app.add_typer(groups_app, name="groups")
app.add_typer(values_app, name="values")
app.add_typer(faults_app, name="faults")
console = Console()


def _ensure_transport_args(tcp_host: Optional[str], tcp_port: Optional[int], serial_port: Optional[str]) -> None:
    tcp = tcp_host or tcp_port
    if tcp and serial_port:
        raise typer.BadParameter("TCP and serial transports are mutually exclusive")
    if not tcp and not serial_port:
        raise typer.BadParameter("Specify either --tcp-host/--tcp-port or --serial-port")


def _load_config_dict(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    fmt = "yaml" if path.suffix.lower() in {".yaml", ".yml"} else "json"
    if fmt == "yaml":
        if yaml is None:
            raise typer.BadParameter("PyYAML is required for YAML configs")
        data = yaml.safe_load(text) or {}
    else:
        data = json.loads(text or "{}")
    if not isinstance(data, dict):
        raise typer.BadParameter("Configuration file must contain a JSON/YAML object")
    return data, fmt


def _write_config_dict(path: Path, data: dict, fmt: str) -> None:
    if fmt == "yaml":
        if yaml is None:
            raise typer.BadParameter("PyYAML is required for YAML configs")
        text = yaml.safe_dump(data, sort_keys=False)
    else:
        text = json.dumps(data, indent=2)
    if not text.endswith("\n"):
        text += "\n"
    path.write_text(text, encoding="utf-8")


async def _interactive_console(device: MockDevice) -> None:
    console.print("[bold cyan]Interactive console ready[/] — commands: help, groups, set, rule, fault, events, snapshot, quit")
    while True:
        try:
            raw = await asyncio.to_thread(input, "mock-server> ")
        except (EOFError, KeyboardInterrupt):
            console.print("Exiting console...")
            return
        if not raw:
            continue
        parts = raw.strip().split()
        if not parts:
            continue
        cmd = parts[0].lower()
        if cmd in {"quit", "exit"}:
            return
        if cmd == "help":
            console.print("Available commands: groups | set <type> <addr> <value> | rule <addr> <mode> [value] | fault <field> <value> | events | snapshot | quit")
            continue
        if cmd == "groups":
            table = Table(title="Configured Register Groups")
            table.add_column("Name")
            table.add_column("Type")
            table.add_column("Start")
            table.add_column("Length")
            table.add_column("Writable")
            for dtype in DataType:
                for group in device.groups_for(dtype):
                    table.add_row(group.name, dtype.value, str(group.start), str(group.length), "yes" if group.writable else "no")
            console.print(table)
            continue
        if cmd == "snapshot":
            data = device.snapshot()
            console.print(data)
            continue
        if cmd == "events":
            console.print("Waiting for next diagnostics event (CTRL+C to stop)...")
            try:
                event = await asyncio.wait_for(device.diagnostics.next_event(), timeout=30.0)
                console.print(event)
            except asyncio.TimeoutError:
                console.print("No events in the last 30s")
            continue
        if cmd == "fault" and len(parts) >= 3:
            field = parts[1]
            try:
                value = float(parts[2]) if "." in parts[2] else int(parts[2])
            except ValueError:
                console.print("Invalid fault value")
                continue
            device.diagnostics.update(**{field: value})
            console.print(f"Fault setting {field} updated to {value}")
            continue
        if cmd == "set" and len(parts) >= 4:
            try:
                dtype = parse_data_type(parts[1])
            except ValueError as exc:
                console.print(f"{exc}")
                continue
            try:
                address = int(parts[2])
            except ValueError:
                console.print("Invalid address")
                continue
            try:
                value = int(parts[3], 0)
            except ValueError:
                console.print("Invalid value")
                continue
            try:
                await device.write(dtype, address, [value])
                console.print(f"Wrote value {value} to {dtype.value} {address}")
            except Exception as exc:  # pylint: disable=broad-except
                console.print(f"Write failed: {exc}")
            continue
        if cmd == "rule" and len(parts) >= 3:
            try:
                address = int(parts[1])
            except ValueError:
                console.print("Invalid address")
                continue
            mode = parts[2]
            metadata = {}
            if len(parts) >= 4:
                try:
                    metadata["value"] = int(parts[3], 0)
                except ValueError:
                    metadata["value"] = parts[3]
            from umdt.mock_server.models import RegisterRule, ResponseMode

            rule = RegisterRule(response_mode=ResponseMode(mode), forced_value=metadata.get("value"))
            await device.apply_rule(address, rule)
            console.print(f"Applied rule {mode} to address {address}")
            continue
        console.print(f"Unknown command: {raw}")


async def _run_server(config_path: Path, tcp_host: Optional[str], tcp_port: int, serial_port: Optional[str], serial_baud: int, interactive: bool, pcap_path: Optional[Path] = None) -> None:
    cfg = load_config(config_path)
    device = MockDevice(cfg)
    coordinator = TransportCoordinator(device, unit_id=cfg.unit_id, pcap_path=pcap_path)
    if pcap_path:
        console.print(f"[yellow]PCAP logging enabled: {pcap_path}[/]")
    async def _event_printer(dev: MockDevice) -> None:
        try:
            while True:
                try:
                    event = await dev.diagnostics.next_event()
                except asyncio.CancelledError:
                    break
                # Nicely format event for CLI
                meta = "".join([f" {k}={v}" for k, v in (event.metadata or {}).items()])
                console.print(f"[cyan][{event.timestamp.isoformat()}][/cyan] {event.transport}: {event.description}{meta}")
        except Exception:  # pragma: no cover - defensive
            console.print("Event printer stopped due to error")
    event_task: Optional[asyncio.Task] = None
    try:
        if serial_port:
            await coordinator.start_serial(serial_port, serial_baud)
            transport_label = f"serial {serial_port}@{serial_baud}"
        else:
            await coordinator.start_tcp(tcp_host or "127.0.0.1", tcp_port)
            transport_label = f"tcp {tcp_host or '0.0.0.0'}:{tcp_port}"
        console.print(f"[green]Server running on {transport_label}. Press Ctrl+C to stop.[/]")
        # Start background task to print diagnostics events
        event_task = asyncio.create_task(_event_printer(device))
        if interactive:
            await _interactive_console(device)
        else:
            stop_event = asyncio.Event()
            await stop_event.wait()
    finally:
        # Cancel event printer if running
        if event_task is not None:
            event_task.cancel()
            try:
                await event_task
            except asyncio.CancelledError:
                pass
        await coordinator.stop()


@app.command()
def start(
    config: Path = typer.Option(..., exists=True, readable=True, help="Path to YAML/JSON config"),
    tcp_host: Optional[str] = typer.Option(None, help="TCP host to bind"),
    tcp_port: int = typer.Option(15020, help="TCP port to bind"),
    serial_port: Optional[str] = typer.Option(None, help="Serial port device"),
    serial_baud: int = typer.Option(9600, help="Serial baudrate"),
    interactive: bool = typer.Option(False, help="Launch interactive console for runtime control"),
    pcap: Optional[Path] = typer.Option(None, help="Path to PCAP file for traffic capture"),
):
    """Start the mock server over TCP or serial."""

    _ensure_transport_args(tcp_host, tcp_port, serial_port)
    try:
        asyncio.run(_run_server(config, tcp_host, tcp_port, serial_port, serial_baud, interactive, pcap))
    except KeyboardInterrupt:
        console.print("Stopping server...")


@app.command()
def status(config: Path = typer.Option(..., exists=True, readable=True, help="Config file to inspect")):
    """Print a summary of the configured register groups."""

    cfg = load_config(config)
    table = Table(title="Mock Server Summary")
    table.add_column("Unit ID")
    table.add_column("Groups")
    table.add_column("Latency")
    table.add_column("Transport")
    transport = cfg.transport
    if transport:
        if transport.serial_port:
            label = f"Serial {transport.serial_port}@{transport.serial_baud}"
        else:
            label = f"TCP {transport.tcp_host or '0.0.0.0'}:{transport.tcp_port or 15020}"
    else:
        label = "None"
    table.add_row(str(cfg.unit_id), str(len(cfg.groups)), f"{cfg.latency_ms} ms", label)
    console.print(table)


@groups_app.command("list")
def groups_list(config: Path = typer.Option(..., exists=True, readable=True)):
    """List register groups defined in the config."""

    cfg, _ = _load_config_dict(config)
    groups = cfg.get("groups", []) or []
    table = Table(title=f"Groups in {config}")
    table.add_column("Name")
    table.add_column("Type")
    table.add_column("Start")
    table.add_column("Length")
    table.add_column("Writable")
    for group in groups:
        table.add_row(
            group.get("name", "?"),
            group.get("type", "holding"),
            str(group.get("start", 0)),
            str(group.get("length", 0)),
            "yes" if group.get("writable", True) else "no",
        )
    console.print(table)


@groups_app.command("add")
def groups_add(
    config: Path = typer.Option(..., exists=True),
    name: str = typer.Argument(..., help="Friendly group name"),
    data_type: str = typer.Option("holding", help="Data type: holding/input/coil/discrete"),
    start: int = typer.Option(..., help="Starting address"),
    length: int = typer.Option(..., help="Number of addresses"),
    writable: bool = typer.Option(True, help="Allow writes"),
    description: str = typer.Option("", help="Optional description"),
):
    """Append a register group to the config file."""

    cfg, fmt = _load_config_dict(config)
    try:
        dtype = parse_data_type(data_type).value
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    groups = cfg.setdefault("groups", [])
    if not isinstance(groups, list):
        raise typer.BadParameter("'groups' must be a list")
    groups.append(
        {
            "name": name,
            "type": dtype,
            "start": int(start),
            "length": int(length),
            "writable": bool(writable),
            "description": description,
        }
    )
    _write_config_dict(config, cfg, fmt)
    console.print(f"Added group '{name}' to {config}")


@groups_app.command("remove")
def groups_remove(
    config: Path = typer.Option(..., exists=True),
    name: str = typer.Argument(..., help="Name of the group to remove"),
):
    """Remove a register group by name."""

    cfg, fmt = _load_config_dict(config)
    groups = cfg.get("groups", []) or []
    if not isinstance(groups, list):
        raise typer.BadParameter("'groups' must be a list")
    new_groups = [g for g in groups if g.get("name") != name]
    if len(new_groups) == len(groups):
        console.print(f"No group named '{name}' was found.")
        return
    cfg["groups"] = new_groups
    _write_config_dict(config, cfg, fmt)
    console.print(f"Removed group '{name}'")


@groups_app.command("reset")
def groups_reset(
    config: Path = typer.Option(..., exists=True),
    yes: bool = typer.Option(False, "--yes", help="Confirm reset without prompting"),
):
    """Remove all register groups from the config file."""

    if not yes and not typer.confirm("Remove all register groups?", default=False):
        raise typer.Abort()
    cfg, fmt = _load_config_dict(config)
    cfg["groups"] = []
    _write_config_dict(config, cfg, fmt)
    console.print("All groups cleared")


@values_app.command("set")
def values_set(
    config: Path = typer.Option(..., exists=True),
    address: int = typer.Argument(..., help="Target address"),
    value: int = typer.Argument(..., help="Value to apply"),
    mode: str = typer.Option("frozen-value", help="Rule mode: frozen-value, exception, ignore-write"),
    exception_code: Optional[int] = typer.Option(None, help="Modbus exception code when mode=exception"),
):
    """Apply a register rule (e.g., frozen value or exception) at the config level."""

    cfg, fmt = _load_config_dict(config)
    rules = cfg.setdefault("rules", {})
    if not isinstance(rules, dict):
        raise typer.BadParameter("'rules' must be a mapping")
    rule: dict = {"mode": mode}
    if mode == "frozen-value":
        rule["forced_value"] = value
    if mode == "exception":
        rule["exception_code"] = exception_code or 2
    if mode == "ignore-write":
        rule["ignore_write"] = True
    rules[str(address)] = rule
    _write_config_dict(config, cfg, fmt)
    console.print(f"Rule {mode} applied to address {address}")


@values_app.command("clear")
def values_clear(
    config: Path = typer.Option(..., exists=True),
    address: int = typer.Argument(..., help="Address to clear"),
):
    """Remove a rule for the specified address."""

    cfg, fmt = _load_config_dict(config)
    rules = cfg.get("rules", {})
    if not isinstance(rules, dict):
        console.print("No rules defined")
        return
    if str(address) in rules:
        del rules[str(address)]
        _write_config_dict(config, cfg, fmt)
        console.print(f"Cleared rule for address {address}")
    else:
        console.print(f"No rule set for address {address}")


@faults_app.command("inject")
def faults_inject(
    config: Path = typer.Option(..., exists=True, writable=True),
    field: str = typer.Argument(..., help="Fault field (latency_ms, drop_rate_pct, etc.)"),
    value: float = typer.Argument(..., help="Value to set"),
):
    """Update the default fault profile entry in the config file."""

    cfg, fmt = _load_config_dict(config)
    faults = cfg.setdefault("faults", {})
    if not isinstance(faults, dict):
        raise typer.BadParameter("'faults' must be an object")
    faults[field] = value
    _write_config_dict(config, cfg, fmt)
    console.print(f"Fault '{field}' set to {value}")


if __name__ == "__main__":
    app()
