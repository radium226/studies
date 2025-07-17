import asyncio
import sys
from click import command, argument, group, UNPROCESSED
from loguru import logger

from dbus_fast import BusType
from dbus_fast.aio import MessageBus


async def execute_command_via_dbus(command: list) -> int:
    """Send a command to the D-Bus server for execution and stream output"""
    try:
        logger.info(f"Connecting to D-Bus server to execute: {command}")
        
        # Connect to the session bus
        bus = await MessageBus(bus_type=BusType.SESSION).connect()
        
        # Get the CommandExecutor interface
        executor_introspection = await bus.introspect(
            "com.radium226.CommandExecutor",
            "/com/radium226/CommandExecutor"
        )
        
        executor_proxy = bus.get_proxy_object(
            "com.radium226.CommandExecutor",
            "/com/radium226/CommandExecutor",
            executor_introspection
        )
        
        executor_interface = executor_proxy.get_interface("com.radium226.CommandExecutor")
        
        # Call ExecuteCommand to get the Run instance path
        run_path = await executor_interface.call_execute_command(command)
        logger.info(f"Command started, Run instance path: {run_path}")
        
        # Get the Run interface proxy
        run_introspection = await bus.introspect(
            "com.radium226.CommandExecutor",
            run_path
        )
        
        run_proxy = bus.get_proxy_object(
            "com.radium226.CommandExecutor",
            run_path,
            run_introspection
        )
        
        run_interface = run_proxy.get_interface("com.radium226.Run")
        
        # Set up signal handlers for the specific Run instance
        completion_event = asyncio.Event()
        exit_code = 0
        
        def output_received_handler(output: str):
            """Handle OutputReceived signal from the specific Run instance"""
            print(output, end='', flush=True)
        
        def command_completed_handler(code: int):
            """Handle CommandCompleted signal from the specific Run instance"""
            nonlocal exit_code
            exit_code = code
            completion_event.set()
        
        # Connect signal handlers to the Run instance
        run_interface.on_output_received(output_received_handler)
        run_interface.on_command_completed(command_completed_handler)
        
        # Wait for command completion
        await completion_event.wait()
        
        bus.disconnect()
        return exit_code
        
    except Exception as e:
        error_msg = f"Error communicating with D-Bus server: {str(e)}"
        logger.error(error_msg)
        print(error_msg, file=sys.stderr)
        return 1


async def list_runs() -> None:
    """List all active runs"""
    try:
        # Connect to the session bus
        bus = await MessageBus(bus_type=BusType.SESSION).connect()
        
        # Get the CommandExecutor interface
        executor_introspection = await bus.introspect(
            "com.radium226.CommandExecutor",
            "/com/radium226/CommandExecutor"
        )
        
        executor_proxy = bus.get_proxy_object(
            "com.radium226.CommandExecutor",
            "/com/radium226/CommandExecutor",
            executor_introspection
        )
        
        executor_interface = executor_proxy.get_interface("com.radium226.CommandExecutor")
        
        # Get list of runs
        runs = await executor_interface.call_list_runs()
        
        if not runs:
            print("No active runs")
        else:
            print(f"{'EXECUTION ID':<36} {'STATUS':<10} {'COMMAND':<30} {'START TIME'}")
            print("-" * 100)
            for execution_id, (start_time, command, status) in runs.items():
                command_str = ' '.join(command)
                # Truncate command if too long
                if len(command_str) > 28:
                    command_str = command_str[:25] + "..."
                print(f"{execution_id:<36} {status:<10} {command_str:<30} {start_time}")
        
        bus.disconnect()
        
    except Exception as e:
        logger.error(f"Error listing runs: {str(e)}")
        print(f"Error listing runs: {str(e)}", file=sys.stderr)
        sys.exit(1)


async def attach_to_run(execution_id: str) -> int:
    """Attach to an existing run and stream its output"""
    try:
        # Connect to the session bus
        bus = await MessageBus(bus_type=BusType.SESSION).connect()
        
        # Get the CommandExecutor interface
        executor_introspection = await bus.introspect(
            "com.radium226.CommandExecutor",
            "/com/radium226/CommandExecutor"
        )
        
        executor_proxy = bus.get_proxy_object(
            "com.radium226.CommandExecutor",
            "/com/radium226/CommandExecutor",
            executor_introspection
        )
        
        executor_interface = executor_proxy.get_interface("com.radium226.CommandExecutor")
        
        # Get the Run instance path
        run_path = await executor_interface.call_get_run_path(execution_id)
        logger.info(f"Attaching to run at path: {run_path}")
        
        # Get the Run interface proxy
        run_introspection = await bus.introspect(
            "com.radium226.CommandExecutor",
            run_path
        )
        
        run_proxy = bus.get_proxy_object(
            "com.radium226.CommandExecutor",
            run_path,
            run_introspection
        )
        
        run_interface = run_proxy.get_interface("com.radium226.Run")
        
        # Get run info
        run_info = await run_interface.call_get_info()
        command_str = ' '.join(run_info['command'])
        print(f"Attached to: {command_str} (Status: {run_info['status']})")
        
        if run_info['status'] == 'completed':
            print(f"Command already completed with exit code: {run_info['exit_code']}")
            bus.disconnect()
            return run_info['exit_code']
        
        # Set up signal handlers for the specific Run instance
        completion_event = asyncio.Event()
        exit_code = 0
        
        def output_received_handler(output: str):
            """Handle OutputReceived signal from the specific Run instance"""
            print(output, end='', flush=True)
        
        def command_completed_handler(code: int):
            """Handle CommandCompleted signal from the specific Run instance"""
            nonlocal exit_code
            exit_code = code
            completion_event.set()
        
        # Connect signal handlers to the Run instance
        run_interface.on_output_received(output_received_handler)
        run_interface.on_command_completed(command_completed_handler)
        
        # Wait for command completion
        await completion_event.wait()
        
        bus.disconnect()
        return exit_code
        
    except Exception as e:
        logger.error(f"Error attaching to run: {str(e)}")
        print(f"Error attaching to run: {str(e)}", file=sys.stderr)
        return 1


@group()
def app():
    """Client that sends commands to the D-Bus server for execution"""
    pass


@app.command()
@argument('command_tuple', nargs=-1, type=UNPROCESSED)
def exec(command_tuple: tuple[str, ...]):
    """Execute a command via D-Bus"""
    logger.info(f"Executing command: {command_tuple}")
    # Convert command tuple to list
    command_list = list(command_tuple)
    logger.info(f"Client sending command: {command_list}")
    
    # Execute the command via D-Bus and get exit code
    exit_code = asyncio.run(execute_command_via_dbus(command_list))
    
    # Exit with the command's exit code
    sys.exit(exit_code)


@app.command("list")
def _list():
    """List all active runs"""
    asyncio.run(list_runs())


@app.command()
@argument('execution_id')
def attach(execution_id):
    """Attach to an existing run by execution ID"""
    exit_code = asyncio.run(attach_to_run(execution_id))
    sys.exit(exit_code)