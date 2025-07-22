"""DBus service for ech0 daemon."""

import asyncio
import os

from dbus_fast import BusType
from dbus_fast.aio import MessageBus
from dbus_fast.service import ServiceInterface, method


class Ech0Interface(ServiceInterface):
    """ech0.Ech0 DBus interface."""

    def __init__(self) -> None:
        super().__init__("ech0.Ech0")

    @method()
    async def Echo(self, stdin_fd: "h") -> "h":  # type: ignore[misc]
        stdout_fd, passthrough_fd = os.pipe()
        
        async def _read() -> None:
            stdin_reader = asyncio.StreamReader()
            stdin_transport, stdin_protocol = await asyncio.get_event_loop().connect_read_pipe(
                lambda: asyncio.StreamReaderProtocol(stdin_reader), os.fdopen(stdin_fd, 'rb')
            )
            
            passthrough_writer_transport, passthrough_writer_protocol = await asyncio.get_event_loop().connect_write_pipe(
                lambda: asyncio.Protocol(), os.fdopen(passthrough_fd, 'wb')
            )
            passthrough_writer = asyncio.StreamWriter(passthrough_writer_transport, passthrough_writer_protocol, None, asyncio.get_event_loop())
            
            try:
                while True:
                    data = await stdin_reader.read(1024)
                    if not data:  # EOF - writer closed
                        break
                    
                    data = data.decode("utf-8").upper().encode("utf-8")
                    passthrough_writer.write(data)
                    # await passthrough_writer.drain()
            finally:
                pass
                stdin_transport.close()
                passthrough_writer.close()
                # await passthrough_writer.wait_closed()
            
        asyncio.create_task(_read())
        return stdout_fd


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