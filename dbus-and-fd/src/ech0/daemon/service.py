"""DBus service for ech0 daemon."""

import asyncio
from typing import Any
import os

from dbus_fast import BusType, Message
from dbus_fast.aio import MessageBus
from dbus_fast.service import ServiceInterface, dbus_property, method
import traceback


class Ech0Interface(ServiceInterface):
    """ech0.Ech0 DBus interface."""

    def __init__(self) -> None:
        super().__init__("ech0.Ech0")

    @method()
    async def Echo(self, fd: "h") -> "s":  # type: ignore[misc]
        async def _read() -> None:
            while True:
                loop = asyncio.get_event_loop()
                # Use run_in_executor for the blocking read operation
                data = await loop.run_in_executor(None, os.read, fd, 1024)
                
                if not data:  # EOF - writer closed
                    break
                    
                message = data.decode().strip()
                print(f"Read: {message}")
            
        asyncio.create_task(_read())
        return "OK!"


class Ech0Service:
    """DBus service manager for ech0 daemon."""

    def __init__(self) -> None:
        self.bus: MessageBus | None = None
        self.interface = Ech0Interface()

    async def start(self) -> None:
        """Start the DBus service."""
        self.bus = MessageBus(bus_type=BusType.SESSION, negotiate_unix_fd=True)
        await self.bus.connect()
        
        self.bus.export("/ech0", self.interface)
        await self.bus.request_name("radium226.ech0")
        
        print("ech0 DBus service started on bus name 'ech0'")

    async def run(self) -> None:
        """Run the service indefinitely."""
        if not self.bus:
            await self.start()
        
        try:
            await self.bus.wait_for_disconnect()
        except KeyboardInterrupt:
            print("\nShutting down ech0 service...")
        finally:
            if self.bus:
                self.bus.disconnect()