import asyncio
import subprocess
import uuid
from datetime import datetime
from click import command
from loguru import logger

from dbus_fast.aio import MessageBus
from dbus_fast import BusType, Variant
from dbus_fast.service import ServiceInterface, method, signal


class RunInterface(ServiceInterface):
    def __init__(self, execution_id: str, command: list):
        super().__init__("com.radium226.Run")
        self.execution_id = execution_id
        self.command = command
        self.start_time = datetime.now()
        self.status = "running"
        self.exit_code = None
    
    @signal()
    def OutputReceived(self, output: "s") -> "s":
        """Signal emitted when command output is received"""
        return output
    
    @signal()
    def CommandCompleted(self, exit_code: "i") -> "i":
        """Signal emitted when command execution is completed"""
        return exit_code
    
    @method()
    def GetInfo(self) -> "a{sv}":
        """Get information about this run"""
        return {
            "execution_id": Variant("s", self.execution_id),
            "command": Variant("as", self.command),
            "start_time": Variant("s", self.start_time.isoformat()),
            "status": Variant("s", self.status),
            "exit_code": Variant("i", self.exit_code if self.exit_code is not None else -1)
        }
    
    async def execute_async(self):
        """Execute command asynchronously and stream output"""
        try:
            logger.info(f"Executing command {self.execution_id}: {self.command}")
            
            # Start the process with command as list
            process = await asyncio.create_subprocess_exec(
                *self.command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            
            # Read output line by line and emit signals
            async for line in process.stdout:
                self.OutputReceived(line.decode("utf-8"))
            
            # Wait for process completion
            exit_code = await process.wait()
            
            # Update status
            self.status = "completed"
            self.exit_code = exit_code
            
            # Signal completion
            self.CommandCompleted(exit_code)
            logger.info(f"Command {self.execution_id} completed with exit code: {exit_code}")
            
        except Exception as e:
            error_msg = f"Error executing command '{self.command}': {str(e)}"
            logger.error(error_msg)
            self.status = "error"
            self.exit_code = 1
            self.OutputReceived(f"ERROR: {error_msg}\n")
            self.CommandCompleted(1)


class CommandExecutorInterface(ServiceInterface):
    def __init__(self, bus):
        super().__init__("com.radium226.CommandExecutor")
        self.bus = bus
        self.runs = {}  # Store active run instances
    
    @method()
    def ExecuteCommand(self, command: "as") -> "s":
        """Execute a command and return the D-Bus path to the Run instance"""
        execution_id = str(uuid.uuid4())
        run_path = f"/com/radium226/Run/{execution_id.replace('-', '_')}"
        
        logger.info(f"Starting command execution {execution_id}: {command}")
        
        # Create Run instance
        run_instance = RunInterface(execution_id, command)
        self.runs[execution_id] = run_instance
        
        # Export the run instance to D-Bus
        self.bus.export(run_path, run_instance)
        
        # Start the command execution asynchronously
        asyncio.create_task(run_instance.execute_async())
        
        # Return the D-Bus path to the Run instance
        return run_path
    
    @method()
    def ListRuns(self) -> 'a{s(sass)}':
        """List all runs with their execution IDs, commands, and status"""
        runs_info = {}
        for execution_id, run_instance in self.runs.items():
            runs_info[execution_id] = (
                run_instance.start_time.isoformat(),
                run_instance.command,
                run_instance.status,
            )
        return runs_info
    
    @method()
    def GetRunPath(self, execution_id: "s") -> "s":
        """Get the D-Bus path for a specific run by execution ID"""
        if execution_id not in self.runs:
            raise Exception(f"Run with execution ID {execution_id} not found")
        
        run_path = f"/com/radium226/Run/{execution_id.replace('-', '_')}"
        return run_path


async def main():
    logger.info("Starting D-Bus server...")
    
    # Connect to the session bus
    bus = await MessageBus(bus_type=BusType.SESSION).connect()
    
    # Create and export the service interface
    interface = CommandExecutorInterface(bus)
    bus.export("/com/radium226/CommandExecutor", interface)
    
    # Request the bus name
    await bus.request_name("com.radium226.CommandExecutor")
    
    logger.info("D-Bus server ready and listening for commands")
    
    # Keep the server running
    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        logger.info("Shutting down D-Bus server...")
        bus.disconnect()


@command()
def app():
    logger.info("Starting D-Bus command executor server...")
    asyncio.run(main())