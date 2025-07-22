"""DBus client for ech0 service."""

import asyncio
from typing import Any

from dbus_fast import BusType, Message, MessageType
from dbus_fast.aio import MessageBus


class Ech0Client:
    """DBus client for ech0 service."""

    def __init__(self) -> None:
        self.bus: MessageBus | None = None

    async def connect(self) -> None:
        """Connect to the D-Bus session bus."""
        self.bus = MessageBus(bus_type=BusType.SESSION, negotiate_unix_fd=True)
        await self.bus.connect()

    async def disconnect(self) -> None:
        """Disconnect from the D-Bus."""
        if self.bus:
            self.bus.disconnect()
            self.bus = None

    async def echo(self, stdin_fd: int) -> int:
        """Call the Echo method on the ech0 service."""
        if not self.bus:
            await self.connect()

        # Get the introspection data and create a proxy object
        introspection = await self.bus.introspect("radium226.ech0", "/ech0")
        proxy_object = self.bus.get_proxy_object("radium226.ech0", "/ech0", introspection)
        
        # Get the interface
        interface = proxy_object.get_interface("ech0.Ech0")
        stdout_fd = await interface.call_echo(stdin_fd)
        return stdout_fd

    async def __aenter__(self) -> "Ech0Client":
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.disconnect()