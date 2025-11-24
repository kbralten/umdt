from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional


class DataType(str, Enum):
    """Supported Modbus logical data types."""

    HOLDING = "holding"
    INPUT = "input"
    COIL = "coil"
    DISCRETE = "discrete"


@dataclass(frozen=True)
class DataTypeProperties:
    label: str
    readable: bool
    writable: bool
    bit_based: bool
    read_function: Optional[int]
    write_function: Optional[int]
    pymodbus_read_method: Optional[str]
    pymodbus_write_method: Optional[str]


DATA_TYPE_PROPERTIES: Dict[DataType, DataTypeProperties] = {
    DataType.HOLDING: DataTypeProperties(
        label="Holding Registers",
        readable=True,
        writable=True,
        bit_based=False,
        read_function=0x03,
        write_function=0x10,
        pymodbus_read_method="read_holding_registers",
        pymodbus_write_method="write_registers",
    ),
    DataType.INPUT: DataTypeProperties(
        label="Input Registers",
        readable=True,
        writable=False,
        bit_based=False,
        read_function=0x04,
        write_function=None,
        pymodbus_read_method="read_input_registers",
        pymodbus_write_method=None,
    ),
    DataType.COIL: DataTypeProperties(
        label="Coils",
        readable=True,
        writable=True,
        bit_based=True,
        read_function=0x01,
        write_function=0x0F,
        pymodbus_read_method="read_coils",
        pymodbus_write_method="write_coils",
    ),
    DataType.DISCRETE: DataTypeProperties(
        label="Discrete Inputs",
        readable=True,
        writable=False,
        bit_based=True,
        read_function=0x02,
        write_function=None,
        pymodbus_read_method="read_discrete_inputs",
        pymodbus_write_method=None,
    ),
}


_DATA_TYPE_ALIASES = {
    "h": DataType.HOLDING,
    "holding": DataType.HOLDING,
    "hr": DataType.HOLDING,
    "input": DataType.INPUT,
    "input_register": DataType.INPUT,
    "ir": DataType.INPUT,
    "coil": DataType.COIL,
    "coils": DataType.COIL,
    "c": DataType.COIL,
    "discrete": DataType.DISCRETE,
    "discrete_input": DataType.DISCRETE,
    "di": DataType.DISCRETE,
}


def parse_data_type(value: Optional[str]) -> DataType:
    if not value:
        return DataType.HOLDING
    key = value.strip().lower()
    dtype = _DATA_TYPE_ALIASES.get(key)
    if dtype is None:
        raise ValueError(f"Unknown data type '{value}'")
    return dtype


def is_register_type(dtype: DataType) -> bool:
    return not DATA_TYPE_PROPERTIES[dtype].bit_based


def is_bit_type(dtype: DataType) -> bool:
    return DATA_TYPE_PROPERTIES[dtype].bit_based


def default_data_type() -> DataType:
    return DataType.HOLDING
