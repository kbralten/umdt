import asyncio
import pytest

from umdt.commands.validators import validate_uint16, validate_registers
from umdt.commands.builder import CommandBuilder
from umdt.commands.pipeline import execute_with_write_access
from umdt.core.controller import CoreController
from umdt.transports.mock import MockTransport


def test_validators_ok():
    assert validate_uint16(0) == 0
    assert validate_uint16(65535) == 65535
    assert validate_registers([0, 1, 65535]) == [0, 1, 65535]


def test_validators_bad():
    with pytest.raises(ValueError):
        validate_uint16(-1)
    with pytest.raises(ValueError):
        validate_uint16(70000)
    with pytest.raises(ValueError):
        validate_registers([])


def test_command_builder_and_pipeline():
    # builder -> registers -> bytes
    b = CommandBuilder()
    b.add_uint16(0x4120).add_uint16(0x0000)  # this pattern looks like the float 10.0
    regs = b.get_registers()
    assert isinstance(regs, list)

    # pipeline with mock controller
    mock = MockTransport()
    controller = CoreController(transport=mock)

    async def run():
        await controller.transport.connect()

        async def coro():
            await controller.send_data(b"HELLO")
            return True

        res = await execute_with_write_access(controller, coro, safe_mode_flag=lambda: False)
        return res

    r = asyncio.run(run())
    assert r is True


def test_safe_mode_blocks():
    mock = MockTransport()
    controller = CoreController(transport=mock)

    async def run():
        await controller.transport.connect()

        async def coro():
            await controller.send_data(b"X")
            return True

        with pytest.raises(PermissionError):
            await execute_with_write_access(controller, coro, safe_mode_flag=lambda: True, ui_confirm=lambda: False)

    asyncio.run(run())
