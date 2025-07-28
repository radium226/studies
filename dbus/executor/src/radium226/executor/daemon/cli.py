import asyncio
from typing import NoReturn


from ..shared.dbus.open_bus import open_bus
from ..shared.dbus.executor_interface import ExecutorInterface

from .domain import Executor


def app() -> NoReturn:
    async def coro() -> None:
        async with open_bus() as bus, Executor() as executor:
            await bus.request_name_async("radium226.Executor", 0)
            
            executor_interface = ExecutorInterface(executor)
            executor_path = "/radium226/Executor"
            executor_interface.export_to_dbus(executor_path)
            
            await executor.run_forever()
    
    
    asyncio.run(coro(), debug=True)