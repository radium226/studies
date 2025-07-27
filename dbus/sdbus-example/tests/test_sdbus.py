from typing import AsyncGenerator
from sdbus import (
    DbusInterfaceCommonAsync, 
    dbus_method_async,
    request_default_bus_name_async,
    sd_bus_open_user, 
    request_default_bus_name_async,
    SdBus,
    DbusObjectManagerInterfaceAsync,
    set_default_bus,
    get_default_bus,
    dbus_property_async,
)
from loguru import logger
import asyncio
import pytest
import os


class BarInteface(
    DbusInterfaceCommonAsync,
    interface_name='radium226.foobar.Bar'
):
    @dbus_method_async(
        input_signature="",
        result_signature="",
        method_name="Baz",
    )
    async def baz(self) -> None:
        logger.info("Baz has been called!")



class FooInterface(
    DbusObjectManagerInterfaceAsync,
    interface_name='radium226.foobar.Foo'
):
    
    def __init__(self) -> None:
        super().__init__()

    @dbus_method_async(
        input_signature="i",
        result_signature="o",
        method_name="Bar",
    )
    async def bar(self, id: int) -> str:
        bar_interface = BarInteface()
        bar_path = "/radium226/foobar/Foo/Bar/{id}".format(id=id)
        print(f"bar_path: {bar_path}")

        # await asyncio.sleep(1000)  # Simulate some async work
        
        self.export_with_manager(
            bar_path,
            bar_interface,
        )

        # bar_interface.export_to_dbus(bar_path)
        # await asyncio.sleep(1)  # Ensure the export is complete
        return bar_path
    
    @dbus_property_async(
        property_signature="v",
        property_name="FizzBuzz",
    )
    def fizz_buzz(self) -> str:
        return "FizzBuzz"
        



@pytest.fixture()
async def bus() -> AsyncGenerator[SdBus, None]:
    logger.info("Opening user bus... ")
    bus = sd_bus_open_user()
    logger.info("Opened! ")

    logger.info("Requesting default bus name...")
    await bus.request_name_async('radium226.foobar', 0)
    logger.info("Requested!")
    set_default_bus(bus)
    yield bus

    logger.info("Closing bus...")
    bus.close()
    logger.info("Closed!")


@pytest.fixture()
async def server(bus) -> AsyncGenerator[None, None]:
    assert get_default_bus() is bus, "Bus should be the default bus"
    foo = FooInterface()
    foo.export_to_dbus("/radium226/foobar/Foo")
    yield None


@pytest.fixture()
async def client(bus, server) -> AsyncGenerator[FooInterface, None]:
    yield FooInterface.new_proxy(
        "radium226.foobar",
        "/radium226/foobar/Foo",
    )


def open_file() -> int:
    return os.open("/dev/null", os.O_RDONLY)


def assert_fds_equal(fd1, fd2):
    assert fd1
    assert fd2

    stat1 = os.fstat(fd1)
    stat2 = os.fstat(fd2)

    assert stat1.st_dev == stat2.st_dev
    assert stat1.st_ino == stat2.st_ino
    assert stat1.st_rdev == stat2.st_rdev


async def test_sdbus(server, client, bus):
    foo_interface: FooInterface = client

    bar_path = await client.bar(42)
    assert bar_path == "/radium226/foobar/Foo/Bar/42"
    bar_interface = BarInteface.new_proxy(
        "radium226.foobar",
        bar_path
    )


    await asyncio.sleep(100)  # Ensure the export is complete

    await bar_interface.baz()