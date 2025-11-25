"""CSV/range expansion and parsing helpers.

Centralizes parsing logic for CLI and GUI input handling of:
- CSV-separated lists (e.g., "1,2,5,10")
- Numeric ranges (e.g., "1-10", "500-550")
- Combined inputs (e.g., "1,5-10,20")
- Host/port and serial port parsing
"""

from typing import List, Optional


def expand_csv_or_range(s: Optional[str]) -> List[str]:
    """Expand a CSV string and simple ranges into a list of strings.

    Supports:
    - CSV-separated values: "1,2,3" -> ["1", "2", "3"]
    - Numeric ranges: "1-5" -> ["1", "2", "3", "4", "5"]
    - Combined: "1,5-8,10" -> ["1", "5", "6", "7", "8", "10"]
    - Reverse ranges: "5-1" -> ["5", "4", "3", "2", "1"]
    - Non-numeric strings pass through: "COM1,COM3" -> ["COM1", "COM3"]

    Returns an empty list for None/empty input.
    """
    if not s:
        return []
    out: List[str] = []
    for part in str(s).split(','):
        p = part.strip()
        if not p:
            continue
        # Check if this looks like a range (contains single dash, not at start/end)
        if '-' in p and p.count('-') == 1 and not p.startswith('-') and not p.endswith('-'):
            a, b = p.split('-', 1)
            try:
                ia = int(a, 0)
                ib = int(b, 0)
                step = 1 if ia <= ib else -1
                for v in range(ia, ib + step, step):
                    out.append(str(v))
            except Exception:
                # Not parseable as int range, keep as-is
                out.append(p)
        else:
            out.append(p)
    return out


def expand_int_range(s: Optional[str]) -> List[int]:
    """Expand a CSV/range string into a list of integers.

    Like expand_csv_or_range but returns integers directly.
    Non-numeric values are skipped with no error.

    Examples:
        "1,5-8,10" -> [1, 5, 6, 7, 8, 10]
        "0x10,0x15-0x18" -> [16, 21, 22, 23, 24]

    Returns an empty list for None/empty input.
    """
    result: List[int] = []
    for item in expand_csv_or_range(s):
        try:
            result.append(int(item, 0))
        except (ValueError, TypeError):
            # Skip non-numeric items
            pass
    return result


def parse_host_port(s: str, default_port: int = 502) -> tuple:
    """Parse a host:port string into (host, port) tuple.

    Examples:
        "192.168.1.1:502" -> ("192.168.1.1", 502)
        "192.168.1.1" -> ("192.168.1.1", 502)  # uses default
        "localhost:5020" -> ("localhost", 5020)

    Args:
        s: Input string with optional port
        default_port: Port to use if not specified

    Returns:
        Tuple of (host: str, port: int)

    Raises:
        ValueError: If port is not a valid integer
    """
    s = s.strip()
    if ':' in s:
        host, port_str = s.rsplit(':', 1)
        try:
            port = int(port_str)
        except ValueError:
            raise ValueError(f"Invalid port number: {port_str}")
        return (host, port)
    return (s, default_port)


def parse_serial_baud(s: str, default_baud: int = 9600) -> tuple:
    """Parse a serial port:baud string into (port, baud) tuple.

    Examples:
        "COM5:115200" -> ("COM5", 115200)
        "COM5" -> ("COM5", 9600)  # uses default
        "/dev/ttyUSB0:9600" -> ("/dev/ttyUSB0", 9600)

    Args:
        s: Input string with optional baud rate
        default_baud: Baud rate to use if not specified

    Returns:
        Tuple of (port: str, baud: int)

    Raises:
        ValueError: If baud is not a valid integer
    """
    s = s.strip()
    if ':' in s:
        port, baud_str = s.rsplit(':', 1)
        try:
            baud = int(baud_str)
        except ValueError:
            raise ValueError(f"Invalid baud rate: {baud_str}")
        return (port, baud)
    return (s, default_baud)


def normalize_serial_port(s: str) -> str:
    """Normalize a serial port name.

    Removes leading slashes that may appear from URL parsing.

    Examples:
        "/COM3" -> "COM3"
        "COM5" -> "COM5"
        "/dev/ttyUSB0" -> "dev/ttyUSB0" (Linux paths preserved after first /)

    Args:
        s: Serial port string

    Returns:
        Normalized port name
    """
    if not s:
        return s
    # Remove leading slashes (common with urlparse for Windows paths)
    return s.lstrip("/")
