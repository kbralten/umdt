"""Address parsing and formatting helpers.

Centralizes logic for parsing and formatting Modbus addresses
with hex/decimal format preservation.
"""

from typing import Tuple


def parse_address(s: str) -> Tuple[int, bool]:
    """Parse an address string and return the numeric value plus format info.

    Supports decimal (e.g., "100", "1000") and hexadecimal (e.g., "0x64", "0x3E8")
    formats. Tracks whether the input was hex to preserve format in output.

    Args:
        s: Address string to parse

    Returns:
        Tuple of (numeric_address: int, was_hex: bool)

    Raises:
        ValueError: If the string is not a valid address

    Examples:
        >>> parse_address("100")
        (100, False)
        >>> parse_address("0x64")
        (100, True)
        >>> parse_address("0X64")  # case-insensitive
        (100, True)
    """
    if not s:
        raise ValueError("Address cannot be empty")
    
    s = s.strip()
    if not s:
        raise ValueError("Address cannot be empty")
    
    was_hex = s.lower().startswith("0x")
    
    try:
        numeric = int(s, 0)  # auto-detect base from prefix
    except ValueError:
        raise ValueError(f"Invalid address format: {s}")
    
    return (numeric, was_hex)


def format_address(value: int, as_hex: bool = False) -> str:
    """Format an address value as a string.

    Args:
        value: Numeric address value
        as_hex: If True, format as hex (e.g., "0x64"); otherwise decimal

    Returns:
        Formatted address string

    Examples:
        >>> format_address(100, as_hex=False)
        '100'
        >>> format_address(100, as_hex=True)
        '0x64'
    """
    if as_hex:
        return hex(value)
    return str(value)


def parse_address_range(start: str, end: str) -> Tuple[int, int, bool]:
    """Parse start and end address strings for a range.

    Determines output format based on whether either input was hex.

    Args:
        start: Start address string
        end: End address string

    Returns:
        Tuple of (start_addr: int, end_addr: int, use_hex: bool)

    Raises:
        ValueError: If either address is invalid or start > end
    """
    start_addr, start_was_hex = parse_address(start)
    end_addr, end_was_hex = parse_address(end)
    
    if start_addr > end_addr:
        raise ValueError(f"Start address ({start_addr}) must be <= end address ({end_addr})")
    
    # Use hex output if either input was hex
    use_hex = start_was_hex or end_was_hex
    
    return (start_addr, end_addr, use_hex)
