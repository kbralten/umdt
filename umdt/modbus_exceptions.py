"""Modbus exception code mapping and helpers.

Provides a canonical mapping of standard Modbus exception codes to
human-readable descriptions so CLI and GUI can share the same data.
"""
from typing import Optional

# Standard Modbus exception codes (Modbus Application Protocol spec)
MODBUS_EXCEPTION_CODES = {
    1: "Illegal Function",
    2: "Illegal Data Address",
    3: "Illegal Data Value",
    4: "Slave Device Failure",
    5: "Acknowledge",
    6: "Slave Device Busy",
    8: "Memory Parity Error",
    10: "Gateway Path Unavailable",
    11: "Gateway Target Device Failed to Respond",
}


def get_modbus_exception_text(code: Optional[int]) -> Optional[str]:
    """Return a human-readable description for a Modbus exception code.

    If `code` is None or unknown, returns None.
    """
    if code is None:
        return None
    try:
        return MODBUS_EXCEPTION_CODES.get(int(code))
    except Exception:
        return None
