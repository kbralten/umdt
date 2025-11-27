#!/usr/bin/env python3
"""UMDT Bridge CLI - Soft-Gateway for Modbus Protocol Translation.

This CLI creates a transparent bridge between Modbus Masters and Slaves,
supporting protocol conversion (TCP <-> RTU) with extensibility hooks.

Examples:
    # TCP Master -> RTU Slave (most common: SCADA to legacy device)
    python bridge.py --upstream-port 502 --downstream-serial COM3 --downstream-baud 9600

    # TCP Master -> TCP Slave (TCP-to-TCP relay/inspection)
    python bridge.py --upstream-port 502 --downstream-host 192.168.1.100 --downstream-port 502

    # RTU Master -> TCP Slave (serial master to networked device)
    python bridge.py --upstream-serial COM1 --upstream-baud 9600 --downstream-host 192.168.1.100

    # RTU Master -> RTU Slave (serial-to-serial bridge)
    python bridge.py --upstream-serial COM1 --downstream-serial COM2

See dev_docs/bridge/architecture.md for detailed documentation.
"""
from __future__ import annotations

import asyncio
import logging
import signal
import sys
from typing import Optional

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel
from rich.table import Table

# Ensure the umdt package is importable
if __name__ == "__main__":
    import os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from umdt.bridge import Bridge, FrameType
from umdt.bridge.hooks.pcap_hook import PcapHook

app = typer.Typer(
    name="bridge",
    help="UMDT Bridge - Soft-Gateway for Modbus Protocol Translation",
    add_completion=False,
)
console = Console()


def setup_logging(verbose: bool = False) -> None:
    """Configure logging with rich output."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True, console=console)],
    )


def parse_frame_type(
    tcp_port: Optional[int],
    serial_port: Optional[str],
    side: str,
) -> tuple[FrameType, str]:
    """Determine frame type from provided options."""
    if tcp_port is not None and serial_port is not None:
        console.print(
            f"[red]Error: Cannot specify both TCP port and serial port for {side}[/red]"
        )
        raise typer.Exit(1)

    if serial_port:
        return FrameType.RTU, f"RTU ({serial_port})"
    else:
        return FrameType.TCP, f"TCP (port {tcp_port or 502})"


@app.command()
def start(
    # Upstream (server) options
    upstream_host: str = typer.Option(
        "0.0.0.0",
        "--upstream-host",
        "-uh",
        help="Host address to bind upstream TCP server",
    ),
    upstream_port: Optional[int] = typer.Option(
        None,
        "--upstream-port",
        "-up",
        help="TCP port for upstream server (default: 502)",
    ),
    upstream_serial: Optional[str] = typer.Option(
        None,
        "--upstream-serial",
        "-us",
        help="Serial port for upstream RTU mode (e.g., COM3, /dev/ttyUSB0)",
    ),
    upstream_baud: int = typer.Option(
        9600,
        "--upstream-baud",
        "-ub",
        help="Baud rate for upstream serial port",
    ),
    # Downstream (client) options
    downstream_host: Optional[str] = typer.Option(
        None,
        "--downstream-host",
        "-dh",
        help="Host address of downstream TCP device",
    ),
    downstream_port: Optional[int] = typer.Option(
        None,
        "--downstream-port",
        "-dp",
        help="TCP port of downstream device (default: 502)",
    ),
    downstream_serial: Optional[str] = typer.Option(
        None,
        "--downstream-serial",
        "-ds",
        help="Serial port for downstream RTU mode (e.g., COM3, /dev/ttyUSB0)",
    ),
    downstream_baud: int = typer.Option(
        9600,
        "--downstream-baud",
        "-db",
        help="Baud rate for downstream serial port",
    ),
    # General options
    timeout: float = typer.Option(
        2.0,
        "--timeout",
        "-t",
        help="Timeout for downstream responses in seconds",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Enable debug logging",
    ),
    pcap: Optional[str] = typer.Option(
        None,
        "--pcap",
        "-p",
        help="Write traffic to PCAP file for Wireshark analysis",
    ),
    script: Optional[list[str]] = typer.Option(
        None,
        "--script",
        "-s",
        help="Python script file(s) for logic injection (can be repeated)",
    ),
) -> None:
    """Start the Modbus bridge.

    The bridge accepts connections from Modbus Masters on the upstream side
    and forwards requests to Modbus Slaves on the downstream side.

    \b
    Common configurations:
      TCP→RTU: --upstream-port 502 --downstream-serial COM3
      TCP→TCP: --upstream-port 502 --downstream-host 192.168.1.100
      RTU→TCP: --upstream-serial COM1 --downstream-host 192.168.1.100
      RTU→RTU: --upstream-serial COM1 --downstream-serial COM2
    """
    setup_logging(verbose)

    # Validate and determine upstream configuration
    if upstream_serial is None and upstream_port is None:
        upstream_port = 502  # Default to TCP on port 502

    upstream_type, upstream_desc = parse_frame_type(
        upstream_port, upstream_serial, "upstream"
    )

    # Validate and determine downstream configuration
    if downstream_serial is None and downstream_host is None:
        console.print(
            "[red]Error: Must specify downstream target "
            "(--downstream-host or --downstream-serial)[/red]"
        )
        raise typer.Exit(1)

    downstream_type, downstream_desc = parse_frame_type(
        downstream_port if downstream_host else None,
        downstream_serial,
        "downstream",
    )

    # Display configuration
    table = Table(title="Bridge Configuration", show_header=True)
    table.add_column("Side", style="cyan")
    table.add_column("Type", style="green")
    table.add_column("Connection", style="yellow")

    if upstream_type == FrameType.TCP:
        upstream_conn = f"{upstream_host}:{upstream_port or 502}"
    else:
        upstream_conn = f"{upstream_serial} @ {upstream_baud} baud"

    if downstream_type == FrameType.TCP:
        downstream_conn = f"{downstream_host}:{downstream_port or 502}"
    else:
        downstream_conn = f"{downstream_serial} @ {downstream_baud} baud"

    table.add_row("Upstream (Server)", upstream_type.name, upstream_conn)
    table.add_row("Downstream (Client)", downstream_type.name, downstream_conn)

    console.print(table)
    console.print()

    # Create bridge
    bridge = Bridge(
        upstream_type=upstream_type,
        upstream_host=upstream_host,
        upstream_port=upstream_port or 502,
        upstream_serial_port=upstream_serial,
        upstream_baudrate=upstream_baud,
        downstream_type=downstream_type,
        downstream_host=downstream_host,
        downstream_port=downstream_port or 502,
        downstream_serial_port=downstream_serial,
        downstream_baudrate=downstream_baud,
        timeout=timeout,
        scripts=script,
    )

    # Display script info if loaded
    if script:
        console.print(f"[cyan]Scripts loaded: {', '.join(script)}[/cyan]")

    # Add a logging hook to show traffic
    async def log_request(request, context):
        console.print(
            f"[dim]→ Unit {request.unit_id} FC {request.function_code:02X} "
            f"({len(request.data)} bytes)[/dim]"
        )
        return request

    async def log_response(response, context):
        status = "[red]ERR[/red]" if response.is_exception else "[green]OK[/green]"
        console.print(
            f"[dim]← Unit {response.unit_id} FC {response.function_code:02X} "
            f"{status}[/dim]"
        )
        return response

    bridge.pipeline.add_ingress_hook(log_request)
    bridge.pipeline.add_response_hook(log_response)

    # Set up PCAP logging if requested
    pcap_hook: Optional[PcapHook] = None
    if pcap:
        pcap_hook = PcapHook(pcap)
        bridge.pipeline.add_ingress_hook(pcap_hook.ingress_hook)
        bridge.pipeline.add_response_hook(pcap_hook.response_hook)
        console.print(f"[cyan]PCAP logging enabled: {pcap}[/cyan]")

    # Run bridge
    console.print(Panel.fit("[bold green]Starting bridge...[/bold green]"))

    async def run():
        # Handle graceful shutdown
        loop = asyncio.get_running_loop()
        stop_event = asyncio.Event()

        def signal_handler():
            console.print("\n[yellow]Shutting down...[/yellow]")
            stop_event.set()

        # Register signal handlers
        try:
            loop.add_signal_handler(signal.SIGINT, signal_handler)
            loop.add_signal_handler(signal.SIGTERM, signal_handler)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            pass

        try:
            # Start PCAP logging if configured
            if pcap_hook:
                await pcap_hook.start()

            await bridge.start()
            console.print("[bold green]Bridge running. Press Ctrl+C to stop.[/bold green]")

            # Wait for stop signal or periodic stats display
            while not stop_event.is_set():
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=60)
                except asyncio.TimeoutError:
                    # Display stats periodically
                    stats = bridge.get_stats()
                    pcap_info = ""
                    if pcap_hook and pcap_hook.is_active:
                        pcap_stats = pcap_hook.stats
                        pcap_info = f", {pcap_stats['packets']} packets logged"
                    console.print(
                        f"[dim]Stats: {stats['requests_processed']} requests, "
                        f"{stats['upstream_clients']} clients{pcap_info}[/dim]"
                    )
        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupted[/yellow]")
        finally:
            await bridge.stop()
            if pcap_hook:
                await pcap_hook.stop()
            console.print("[green]Bridge stopped.[/green]")

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


@app.command()
def info() -> None:
    """Display bridge capabilities and usage information."""
    console.print(
        Panel.fit(
            "[bold]UMDT Bridge - Soft-Gateway[/bold]\n\n"
            "The Bridge creates a transparent passthrough between Modbus "
            "Masters and Slaves,\nwith support for protocol conversion "
            "(TCP ↔ RTU) and extensibility hooks.\n\n"
            "[bold]Features:[/bold]\n"
            "  • TCP ↔ RTU protocol conversion (MBAP ↔ CRC framing)\n"
            "  • Multiple upstream client support with request queuing\n"
            "  • Python script injection for custom logic (--script)\n"
            "  • PCAP traffic logging for Wireshark analysis (--pcap)\n\n"
            "[bold]Script API:[/bold]\n"
            "  Scripts can define these hooks:\n"
            "  • on_request(req, ctx) - Intercept/modify/block requests\n"
            "  • on_response(resp, ctx) - Intercept/modify responses\n"
            "  Return ExceptionResponse(code) to reject a request.\n\n"
            "[bold]Common Use Cases:[/bold]\n"
            "  • Connect TCP-only SCADA to legacy RS-485 devices\n"
            "  • Add safety interlocks without modifying PLC firmware\n"
            "  • Inspect and log Modbus traffic transparently\n",
            title="About",
        )
    )


if __name__ == "__main__":
    app()
