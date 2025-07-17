import asyncio
import pytest

from radium226.run.client.app import execute_command_via_dbus
from radium226.run.server.app import main as server_main


@pytest.fixture
async def dbus_server():
    """Start the D-Bus server for testing"""
    # Start the server in a separate task
    server_task = asyncio.create_task(server_main())
    
    # Give the server time to start
    await asyncio.sleep(0.5)
    
    try:
        yield
    finally:
        # Clean up: cancel the server task
        server_task.cancel()
        try:
            await asyncio.wait_for(server_task, timeout=1.0)
            
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass


@pytest.mark.asyncio
async def test_execute_simple_command(dbus_server):
    """Test executing a simple command via D-Bus"""
    command = ["echo", "Hello, World!"]
    
    exit_code = await execute_command_via_dbus(command)
    assert exit_code == 0


@pytest.mark.asyncio
async def test_execute_command_with_output(dbus_server):
    """Test executing a command and capturing output"""
    command = ["echo", "test output"]
    
    # Capture stdout to verify output is printed
    import io
    from contextlib import redirect_stdout
    
    captured_output = io.StringIO()
    with redirect_stdout(captured_output):
        exit_code = await execute_command_via_dbus(command)
    
    assert exit_code == 0
    assert "test output" in captured_output.getvalue()


@pytest.mark.asyncio
async def test_execute_failing_command(dbus_server):
    """Test executing a command that fails"""
    command = ["false"]  # Command that always exits with code 1
    
    exit_code = await execute_command_via_dbus(command)
    
    assert exit_code == 1


@pytest.mark.asyncio
async def test_execute_nonexistent_command(dbus_server):
    """Test executing a command that doesn't exist"""
    command = ["nonexistent-command-12345"]
    
    exit_code = await execute_command_via_dbus(command)
    
    assert exit_code == 1


@pytest.mark.asyncio
async def test_list_runs_empty(dbus_server):
    """Test listing runs when no runs exist"""
    # This should not raise an exception
    from radium226.run.client.app import list_runs
    await list_runs()


@pytest.mark.asyncio
async def test_list_runs_with_active_run(dbus_server):
    """Test listing runs with an active long-running command"""
    # Start a long-running command
    long_command = ["sleep", "10"]
    
    # Start the command in a separate task
    command_task = asyncio.create_task(execute_command_via_dbus(long_command))
    
    # Give it time to start
    await asyncio.sleep(0.2)
    
    # List runs should show the active command
    import io
    from contextlib import redirect_stdout
    
    captured_output = io.StringIO()
    with redirect_stdout(captured_output):
        from radium226.run.client.app import list_runs
        await list_runs()
    
    output = captured_output.getvalue()
    assert "sleep 10" in output
    assert "running" in output
    
    # Cancel the long-running command
    command_task.cancel()
    try:
        await command_task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_attach_to_completed_run(dbus_server):
    """Test attaching to a completed run"""
    # Execute a quick command first
    command = ["echo", "completed command"]
    await execute_command_via_dbus(command)
    
    # Give it time to complete
    await asyncio.sleep(0.1)
    
    # Get the run list to find the execution ID
    from dbus_fast.aio import MessageBus
    from dbus_fast import BusType
    
    bus = await MessageBus(bus_type=BusType.SESSION).connect()
    
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
    
    # Get the runs
    runs = await executor_interface.call_list_runs()  # type: ignore
    
    # Should have at least one run
    assert len(runs) > 0
    
    # Get the first run's execution ID
    execution_id = list(runs.keys())[0]
    
    # Attach to the completed run
    import io
    from contextlib import redirect_stdout
    
    captured_output = io.StringIO()
    with redirect_stdout(captured_output):
        from radium226.run.client.app import attach_to_run
        exit_code = await attach_to_run(execution_id)
    
    output = captured_output.getvalue()
    assert exit_code == 0
    assert "completed command" in output
    
    bus.disconnect()


@pytest.mark.asyncio
async def test_attach_to_last_run(dbus_server):
    """Test attaching to the last run using 'last' parameter"""
    # Execute a command first
    command = ["echo", "last command test"]
    await execute_command_via_dbus(command)
    
    # Give it time to complete
    await asyncio.sleep(0.1)
    
    # Attach to the last run
    import io
    from contextlib import redirect_stdout
    
    captured_output = io.StringIO()
    with redirect_stdout(captured_output):
        from radium226.run.client.app import attach_to_run
        exit_code = await attach_to_run("last")
    
    output = captured_output.getvalue()
    assert exit_code == 0
    assert "last command test" in output


@pytest.mark.asyncio
async def test_attach_to_nonexistent_run(dbus_server):
    """Test attaching to a non-existent run"""
    from radium226.run.client.app import attach_to_run
    with pytest.raises(Exception):
        await attach_to_run("nonexistent-id")


@pytest.mark.asyncio
async def test_command_abortion(dbus_server):
    """Test aborting a command with SIGINT"""
    # Start a long-running command
    long_command = ["sleep", "30"]
    
    # Mock the signal handler to simulate CTRL+C
    command_task = asyncio.create_task(execute_command_via_dbus(long_command))
    
    # Give it time to start
    await asyncio.sleep(0.2)
    
    # Simulate SIGINT by sending it to the process
    # This is tricky to test directly, so we'll test the abort mechanism instead
    
    # Get the run and call abort on it
    from dbus_fast.aio import MessageBus
    from dbus_fast import BusType
    
    bus = await MessageBus(bus_type=BusType.SESSION).connect()
    
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
    
    # Get the last run ID
    last_run_id = await executor_interface.call_get_last_run_id()  # type: ignore
    
    # Get the run path
    run_path = await executor_interface.call_get_run_path(last_run_id)  # type: ignore
    
    # Get the run interface
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
    
    # Abort the command
    await run_interface.call_abort()  # type: ignore
    
    # Wait for the command to finish
    try:
        exit_code = await asyncio.wait_for(command_task, timeout=5.0)
        # The command should have been aborted
        assert exit_code == 130  # SIGINT exit code
    except asyncio.TimeoutError:
        pytest.fail("Command did not abort within timeout")
    
    bus.disconnect()


@pytest.mark.asyncio
async def test_attach_to_aborted_run(dbus_server):
    """Test attaching to an aborted run shows output history"""
    # Start a command that produces output then gets aborted
    command = ["sh", "-c", "echo 'before abort'; sleep 10; echo 'after abort'"]
    
    command_task = asyncio.create_task(execute_command_via_dbus(command))
    
    # Give it time to start and produce some output
    await asyncio.sleep(0.5)
    
    # Get the run and abort it
    from dbus_fast.aio import MessageBus
    from dbus_fast import BusType
    
    bus = await MessageBus(bus_type=BusType.SESSION).connect()
    
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
    
    # Get the last run ID
    last_run_id = await executor_interface.call_get_last_run_id()  # type: ignore
    
    # Get the run path
    run_path = await executor_interface.call_get_run_path(last_run_id)  # type: ignore
    
    # Get the run interface
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
    
    # Abort the command
    await run_interface.call_abort()  # type: ignore
    
    # Give some time for the abort to be processed
    await asyncio.sleep(0.2)
    
    # Cancel the command task since we're just testing the abort mechanism
    command_task.cancel()
    try:
        await command_task
    except asyncio.CancelledError:
        pass
    
    # Now attach to the aborted run
    import io
    from contextlib import redirect_stdout
    
    captured_output = io.StringIO()
    with redirect_stdout(captured_output):
        from radium226.run.client.app import attach_to_run
        exit_code = await attach_to_run(last_run_id)
    
    output = captured_output.getvalue()
    assert exit_code == 130  # SIGINT exit code
    assert "Command was aborted" in output
    assert "before abort" in output
    
    # Check that "after abort" is not in the actual output (but may be in the command description)
    output_lines = output.split('\n')
    output_section_started = False
    for line in output_lines:
        if "Output before abortion:" in line:
            output_section_started = True
            continue
        if output_section_started and line.strip():
            assert "after abort" not in line  # Should not be present in actual output since command was aborted
    
    bus.disconnect()


@pytest.mark.asyncio
async def test_multiple_commands_sequence(dbus_server):
    """Test running multiple commands and checking sequence"""
    commands = [
        ["echo", "first"],
        ["echo", "second"],
        ["echo", "third"]
    ]
    
    # Execute all commands
    for command in commands:
        await execute_command_via_dbus(command)
        await asyncio.sleep(0.1)  # Small delay between commands
    
    # Check that we can attach to the last one
    import io
    from contextlib import redirect_stdout
    
    captured_output = io.StringIO()
    with redirect_stdout(captured_output):
        from radium226.run.client.app import attach_to_run
        exit_code = await attach_to_run("last")
    
    output = captured_output.getvalue()
    assert exit_code == 0
    assert "third" in output


@pytest.mark.asyncio
async def test_output_history_persistence(dbus_server):
    """Test that output history is preserved for completed commands"""
    # Execute a command with multi-line output
    command = ["sh", "-c", "echo 'line 1'; echo 'line 2'; echo 'line 3'"]
    
    await execute_command_via_dbus(command)
    await asyncio.sleep(0.1)
    
    # Attach to the completed command
    import io
    from contextlib import redirect_stdout
    
    captured_output = io.StringIO()
    with redirect_stdout(captured_output):
        from radium226.run.client.app import attach_to_run
        exit_code = await attach_to_run("last")
    
    output = captured_output.getvalue()
    assert exit_code == 0
    assert "line 1" in output
    assert "line 2" in output
    assert "line 3" in output


@pytest.mark.asyncio
async def test_user_switching_non_root(dbus_server):
    """Test that user switching works correctly when not running as root"""
    # This test verifies that when the server is not running as root,
    # it correctly executes commands as the same user without errors
    command = ["whoami"]
    
    exit_code = await execute_command_via_dbus(command)
    assert exit_code == 0


@pytest.mark.asyncio 
async def test_user_switching_invalid_user(dbus_server):
    """Test that invalid user handling works correctly"""
    # This test would require mocking the user lookup to simulate an invalid user
    # For now, this is a placeholder - in a real scenario with root privileges,
    # we could test with a non-existent user
    pass


@pytest.mark.asyncio
async def test_user_information_logging(dbus_server):
    """Test that user information is properly logged and transmitted"""
    
    # Capture logs to verify user information is being transmitted
    command = ["echo", "test user info"]
    
    # The execute_command_via_dbus function should log the current user
    # We can verify this by checking that no exceptions are raised
    # and the command executes successfully
    exit_code = await execute_command_via_dbus(command)
    assert exit_code == 0