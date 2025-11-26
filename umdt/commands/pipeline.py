from typing import Callable, Awaitable, Optional
from umdt.core.controller import CoreController


async def execute_with_write_access(
    controller: CoreController,
    coro_fn: Callable[[], Awaitable],
    safe_mode_flag: Callable[[], bool] = lambda: False,
    ui_confirm: Optional[Callable[[], bool]] = None,
):
    """Execute `coro_fn` while enforcing SAFE_MODE and acquiring the transport lock.

    - `safe_mode_flag` should return True when safe mode is active.
    - `ui_confirm` is a sync callable that returns True when the user has approved the action.
    """
    if safe_mode_flag():
        if ui_confirm is None:
            raise PermissionError("Operation blocked by SAFE_MODE")
        approved = ui_confirm()
        if not approved:
            raise PermissionError("User denied operation under SAFE_MODE")

    async with controller.request_write_access():
        return await coro_fn()
