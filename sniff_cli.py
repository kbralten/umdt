import typer
import asyncio
import time
from typing import Optional
from rich.console import Console
from umdt.core.sniffer import Sniffer
from umdt.utils.parsing import normalize_serial_port

_HAS_PYSERIAL = True
try:
    import serial
except ImportError:
    _HAS_PYSERIAL = False

app = typer.Typer()
console = Console()

@app.command()
def sniff(
    serial: str = typer.Argument(..., help="Serial port (e.g. COM5)"),
    baud: int = typer.Option(9600, help="Baud rate"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Output database file (default: umdt_traffic.db)"),
):
    """Passive sniffer for Modbus RTU traffic.

    Captures all traffic on the serial port, reassembles frames using a heuristic
    sliding window, and logs them to a SQLite database. Live traffic is also
    printed to the console.
    """
    if not _HAS_PYSERIAL:
        console.print("pyserial is required for sniffing")
        raise typer.Exit(code=1)
        
    sp = normalize_serial_port(serial)
    
    console.print(f"[bold green]Starting Sniffer on {sp} @ {baud} baud...[/bold green]")
    console.print("Press Ctrl-C to stop.")
    
    def print_frame(frame):
        raw = frame['raw']
        ts = frame['timestamp']
        hex_str = " ".join(f"{b:02X}" for b in raw)
        # Basic decoding (ID, FC)
        slave = raw[0]
        fc = raw[1]
        desc = f"Slave {slave} FC {fc}"
        console.print(f"[{time.strftime('%H:%M:%S', time.localtime(ts))}] {desc:<15} | {hex_str}")

    sniffer = Sniffer(port=sp, baudrate=baud, db_path=output, on_frame=print_frame)
    
    async def run_sniffer():
        await sniffer.start()
        try:
            # Keep running until cancelled
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            await sniffer.stop()

    try:
        asyncio.run(run_sniffer())
    except KeyboardInterrupt:
        console.print("\nSniffer stopped.")

if __name__ == "__main__":
    app()
