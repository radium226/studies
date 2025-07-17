import asyncio
import os
import pwd
import signal
import sys
from click import argument, group, UNPROCESSED
from loguru import logger

from dbus_fast import BusType
from dbus_fast.aio import MessageBus


async def _connect_to_server_bus() -> tuple[MessageBus, BusType]:
    """Connect to the D-Bus server, trying system bus first, then session bus"""
    # Try system bus first (in case there's a root server running)
    try:
        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        # Test if the service is available on the system bus
        try:
            await bus.introspect(
                "com.radium226.CommandExecutor", "/com/radium226/CommandExecutor"
            )
            logger.info("Found CommandExecutor service on system bus")
            return bus, BusType.SYSTEM
        except Exception:
            # Service not available on system bus, disconnect and try session bus
            bus.disconnect()
    except Exception as e:
        logger.debug(f"Could not connect to system bus: {e}")

    # Fall back to session bus
    try:
        bus = await MessageBus(bus_type=BusType.SESSION).connect()
        # Test if the service is available on the session bus
        try:
            await bus.introspect(
                "com.radium226.CommandExecutor", "/com/radium226/CommandExecutor"
            )
            logger.info("Found CommandExecutor service on session bus")
            return bus, BusType.SESSION
        except Exception:
            bus.disconnect()
            raise Exception("CommandExecutor service not found on session bus")
    except Exception as e:
        raise Exception(f"Could not connect to any D-Bus bus: {e}")


async def execute_command_via_dbus(command: list[str]) -> int:
    """Send a command to the D-Bus server for execution and stream output"""
    try:
        logger.info(f"Connecting to D-Bus server to execute: {command}")

        # Try to connect to the appropriate bus
        bus, bus_type_used = await _connect_to_server_bus()
        logger.info(f"Connected to D-Bus server on {bus_type_used.name.lower()} bus")

        # Get the CommandExecutor interface
        executor_introspection = await bus.introspect(
            "com.radium226.CommandExecutor", "/com/radium226/CommandExecutor"
        )

        executor_proxy = bus.get_proxy_object(
            "com.radium226.CommandExecutor",
            "/com/radium226/CommandExecutor",
            executor_introspection,
        )

        executor_interface = executor_proxy.get_interface(
            "com.radium226.CommandExecutor"
        )

        # Get current user information
        current_uid = os.getuid()
        current_user = pwd.getpwuid(current_uid).pw_name
        logger.info(f"Executing command as user: {current_user} (uid: {current_uid})")

        # Get current environment variables and working directory
        env_vars = dict(os.environ)
        working_dir = os.getcwd()
        logger.info(f"Working directory: {working_dir}, environment variables: {len(env_vars)} vars")

        # Call ExecuteCommand to create the Run instance (but not start it yet)
        run_path = await executor_interface.call_execute_command(command, current_user, env_vars, working_dir)  # type: ignore
        logger.info(f"Command created, Run instance path: {run_path}")

        # Get the Run interface proxy
        run_introspection = await bus.introspect(
            "com.radium226.CommandExecutor", run_path
        )

        run_proxy = bus.get_proxy_object(
            "com.radium226.CommandExecutor", run_path, run_introspection
        )

        run_interface = run_proxy.get_interface("com.radium226.Run")

        # Set up signal handlers for the specific Run instance
        completion_event = asyncio.Event()
        exit_code = 0
        interrupted = False

        def output_received_handler(output: str) -> None:
            """Handle OutputReceived signal from the specific Run instance"""
            print(output, end="", flush=True)

        def command_completed_handler(code: int) -> None:
            """Handle CommandCompleted signal from the specific Run instance"""
            nonlocal exit_code
            exit_code = code
            completion_event.set()

        async def handle_interrupt() -> None:
            """Handle CTRL+C interrupt by aborting the command"""
            nonlocal interrupted
            if not interrupted:
                interrupted = True
                logger.info("Interrupt received, aborting command...")
                try:
                    await run_interface.call_abort()  # type: ignore
                    logger.info("Command aborted")
                except Exception as e:
                    logger.error(f"Error aborting command: {e}")
                completion_event.set()

        # Set up SIGINT handler
        def sigint_handler() -> None:
            asyncio.create_task(handle_interrupt())

        # Install signal handler BEFORE starting the command
        loop = asyncio.get_event_loop()
        loop.add_signal_handler(signal.SIGINT, sigint_handler)

        # Connect signal handlers to the Run instance
        run_interface.on_output_received(output_received_handler)  # type: ignore
        run_interface.on_command_completed(command_completed_handler)  # type: ignore

        # Now start the actual command execution
        await run_interface.call_do_run()  # type: ignore
        logger.info("Command execution started")

        # Wait for command completion or interruption
        await completion_event.wait()

        # Remove signal handler
        loop.remove_signal_handler(signal.SIGINT)

        bus.disconnect()
        return 130 if interrupted else exit_code  # 130 is standard exit code for SIGINT

    except Exception as e:
        error_msg = f"Error communicating with D-Bus server: {str(e)}"
        logger.error(error_msg)
        print(error_msg, file=sys.stderr)
        return 1


async def list_runs() -> None:
    """List all active runs"""
    try:
        # Try to connect to the appropriate bus
        bus, bus_type_used = await _connect_to_server_bus()
        logger.debug(
            f"Connected to D-Bus server on {bus_type_used.name.lower()} bus for listing runs"
        )

        # Get the CommandExecutor interface
        executor_introspection = await bus.introspect(
            "com.radium226.CommandExecutor", "/com/radium226/CommandExecutor"
        )

        executor_proxy = bus.get_proxy_object(
            "com.radium226.CommandExecutor",
            "/com/radium226/CommandExecutor",
            executor_introspection,
        )

        executor_interface = executor_proxy.get_interface(
            "com.radium226.CommandExecutor"
        )

        # Get list of runs
        runs = await executor_interface.call_list_runs()  # type: ignore

        if not runs:
            print("No active runs")
        else:
            print(f"{'EXECUTION ID':<36} {'STATUS':<10} {'COMMAND':<30} {'START TIME'}")
            print("-" * 100)
            for execution_id, (start_time, command, status) in runs.items():
                command_str = " ".join(command)
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
        # Try to connect to the appropriate bus
        logger.debug("Getting message bus...")
        bus, bus_type_used = await _connect_to_server_bus()
        logger.debug(
            f"Connected to D-Bus server on {bus_type_used.name.lower()} bus for attaching"
        )

        # Get the CommandExecutor interface
        logger.debug("Getting CommandExecutor interface...")
        executor_introspection = await bus.introspect(
            "com.radium226.CommandExecutor", "/com/radium226/CommandExecutor"
        )

        logger.debug("Creating executor proxy...")
        executor_proxy = bus.get_proxy_object(
            "com.radium226.CommandExecutor",
            "/com/radium226/CommandExecutor",
            executor_introspection,
        )

        logger.debug("Creating executor proxy...")
        executor_interface = executor_proxy.get_interface(
            "com.radium226.CommandExecutor"
        )

        # Handle 'last' parameter by getting the most recent run ID
        if execution_id.lower() == "last":
            logger.debug("Getting last run ID...")
            execution_id = await executor_interface.call_get_last_run_id()  # type: ignore
            logger.info(f"Last run ID: {execution_id}")

        # Get the Run instance path
        logger.debug(f"Getting run path for execution ID: {execution_id}")
        try:
            run_path = await executor_interface.call_get_run_path(execution_id)  # type: ignore
            logger.info(f"Attaching to run at path: {run_path}")
        except Exception as e:
            # Re-raise exceptions for non-existent runs
            if "not found" in str(e):
                raise
            else:
                logger.error(f"Error getting run path: {e}")
                raise

        # Get the Run interface proxy
        logger.debug("Getting Run interface proxy...")
        run_introspection = await bus.introspect(
            "com.radium226.CommandExecutor", run_path
        )

        logger.debug("Creating run proxy...")
        run_proxy = bus.get_proxy_object(
            "com.radium226.CommandExecutor", run_path, run_introspection
        )

        logger.debug("Getting Run interface...")
        run_interface = run_proxy.get_interface("com.radium226.Run")
        logger.debug("run_interface={run_interface}", run_interface=run_interface)

        # Get run info
        logger.debug("Getting run info...")
        run_info = await run_interface.call_get_info()  # type: ignore
        logger.debug(f"Run info: {run_info}")
        command = run_info["command"].value
        command_str = " ".join(command)
        status = run_info["status"].value
        print(f"Attached to: {command_str} (Status: {status})")

        if status == "completed":
            print(f"Command already completed with exit code: {run_info['exit_code']}")

            # Get and display the output history
            try:
                output_history = await run_interface.call_get_output_history()  # type: ignore
                if output_history:
                    print("Command output:")
                    for output_line in output_history:
                        print(output_line, end="")
                else:
                    print("No output was captured.")
            except Exception as e:
                logger.error(f"Error retrieving output history: {e}")

            exit_code = int(run_info["exit_code"].value)
            bus.disconnect()
            return exit_code

        if status == "aborted":
            print("Command was aborted")

            # Get and display the output history
            try:
                output_history = await run_interface.call_get_output_history()  # type: ignore
                if output_history:
                    print("Output before abortion:")
                    for output_line in output_history:
                        print(output_line, end="")
                else:
                    print("No output was captured before abortion.")
            except Exception as e:
                logger.error(f"Error retrieving output history: {e}")

            bus.disconnect()
            return 130

        # # Set up signal handlers for the specific Run instance
        completion_event = asyncio.Event()
        exit_code = 0

        def output_received_handler(output: str) -> None:
            """Handle OutputReceived signal from the specific Run instance"""
            print(output, end="", flush=True)

        def command_completed_handler(code: int) -> None:
            """Handle CommandCompleted signal from the specific Run instance"""
            nonlocal exit_code
            exit_code = code
            completion_event.set()

        # Connect signal handlers to the Run instance
        run_interface.on_output_received(output_received_handler)  # type: ignore
        run_interface.on_command_completed(command_completed_handler)  # type: ignore

        # Wait for command completion
        await completion_event.wait()

        bus.disconnect()
        return exit_code

    except Exception as e:
        # Re-raise specific exceptions that should propagate
        if "not found" in str(e):
            raise
        logger.error(f"Error attaching to run: {str(e)}")
        print(f"Error attaching to run: {str(e)}", file=sys.stderr)
        return 1


async def trigger_cleanup() -> None:
    """Trigger manual cleanup of old runs"""
    try:
        bus, bus_type = await _connect_to_server_bus()

        try:
            # Get the CommandExecutor interface
            introspection = await bus.introspect(
                "com.radium226.CommandExecutor", "/com/radium226/CommandExecutor"
            )
            proxy = bus.get_proxy_object(
                "com.radium226.CommandExecutor",
                "/com/radium226/CommandExecutor",
                introspection,
            )
            interface = proxy.get_interface("com.radium226.CommandExecutor")

            # Trigger cleanup and get count
            cleaned_count = await interface.call_cleanup_old_runs()  # type: ignore

            if cleaned_count > 0:
                print(f"✅ Cleaned up {cleaned_count} old runs")
            else:
                print("ℹ️ No runs needed cleanup")

        finally:
            bus.disconnect()

    except Exception as e:
        logger.error(f"Error triggering cleanup: {str(e)}")
        print(f"Error triggering cleanup: {str(e)}", file=sys.stderr)


async def show_cleanup_stats() -> None:
    """Show cleanup statistics and configuration"""
    try:
        bus, bus_type = await _connect_to_server_bus()

        try:
            # Get the CommandExecutor interface
            introspection = await bus.introspect(
                "com.radium226.CommandExecutor", "/com/radium226/CommandExecutor"
            )
            proxy = bus.get_proxy_object(
                "com.radium226.CommandExecutor",
                "/com/radium226/CommandExecutor",
                introspection,
            )
            interface = proxy.get_interface("com.radium226.CommandExecutor")

            # Get cleanup stats
            stats = await interface.call_get_cleanup_stats()  # type: ignore

            print("📊 Cleanup Statistics & Configuration")
            print("=" * 40)
            print(f"Total runs:      {stats['total_runs'].value}")
            print(f"Running:         {stats['running_runs'].value}")
            print(f"Completed:       {stats['completed_runs'].value}")
            print(f"Aborted:         {stats['aborted_runs'].value}")
            print(f"Error:           {stats['error_runs'].value}")
            print(f"Oldest run age:  {stats['oldest_run_age_hours'].value:.1f} hours")
            print()
            print("🔧 Cleanup Configuration")
            print("=" * 40)
            print(f"Max age:         {stats['max_age_hours'].value} hours")
            print(f"Max completed:   {stats['max_completed_runs'].value} runs")
            print(f"Max total:       {stats['max_total_runs'].value} runs")
            print(
                f"Cleanup interval: {stats['cleanup_interval_minutes'].value} minutes"
            )
            print(f"Keep running:    {stats['keep_running'].value}")

        finally:
            bus.disconnect()

    except Exception as e:
        logger.error(f"Error getting cleanup stats: {str(e)}")
        print(f"Error getting cleanup stats: {str(e)}", file=sys.stderr)


async def remove_run(execution_id: str) -> None:
    """Remove a specific run by execution ID"""
    try:
        bus, bus_type = await _connect_to_server_bus()

        try:
            # Get the CommandExecutor interface
            introspection = await bus.introspect(
                "com.radium226.CommandExecutor", "/com/radium226/CommandExecutor"
            )
            proxy = bus.get_proxy_object(
                "com.radium226.CommandExecutor",
                "/com/radium226/CommandExecutor",
                introspection,
            )
            interface = proxy.get_interface("com.radium226.CommandExecutor")

            # Remove the run
            removed = await interface.call_remove_run(execution_id)  # type: ignore

            if removed:
                print(f"✅ Removed run {execution_id}")
            else:
                print(f"❌ Run {execution_id} not found or cannot be removed")

        finally:
            bus.disconnect()

    except Exception as e:
        logger.error(f"Error removing run: {str(e)}")
        print(f"Error removing run: {str(e)}", file=sys.stderr)


@group()
def app() -> None:
    """Client that sends commands to the D-Bus server for execution"""
    pass


@app.command()
@argument("command_tuple", nargs=-1, type=UNPROCESSED)
def exec(command_tuple: tuple[str, ...]) -> None:
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
def _list() -> None:
    """List all active runs"""
    asyncio.run(list_runs())


@app.command()
@argument("execution_id")
def attach(execution_id: str) -> None:
    """Attach to an existing run by execution ID or 'last' for the most recent run"""
    exit_code = asyncio.run(attach_to_run(execution_id))
    sys.exit(exit_code)


@app.command()
def cleanup() -> None:
    """Manually trigger cleanup of old runs"""
    asyncio.run(trigger_cleanup())


@app.command()
def stats() -> None:
    """Show cleanup statistics and configuration"""
    asyncio.run(show_cleanup_stats())


@app.command()
@argument("execution_id")
def remove(execution_id: str) -> None:
    """Remove a specific run by execution ID"""
    asyncio.run(remove_run(execution_id))
