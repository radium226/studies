import asyncio
import uuid
from datetime import datetime
from click import command
from loguru import logger

from dbus_fast.aio import MessageBus
from dbus_fast import BusType, Variant
from dbus_fast.service import ServiceInterface, method, signal


class RunInterface(ServiceInterface):
    def __init__(self, execution_id: str, command: list[str], sequence_number: int):
        super().__init__("com.radium226.Run")
        self.execution_id = execution_id
        self.command = command
        self.start_time = datetime.now()
        self.status = "running"
        self.exit_code: int | None = None
        self.process: asyncio.subprocess.Process | None = None
        self.aborted = False
        self.output_history: list[str] = []
        self.sequence_number = sequence_number
    
    @signal()
    def OutputReceived(self, output: "s") -> "s":  # type: ignore  # noqa: F821
        """Signal emitted when command output is received"""
        self.output_history.append(output)
        return output
    
    @signal()
    def CommandCompleted(self, exit_code: "i") -> "i":  # type: ignore  # noqa: F821
        """Signal emitted when command execution is completed"""
        return exit_code
    
    @method()
    def GetInfo(self) -> "a{sv}":  # type: ignore  # noqa: F722
        """Get information about this run"""
        return {
            "execution_id": Variant("s", self.execution_id),
            "command": Variant("as", self.command),
            "start_time": Variant("s", self.start_time.isoformat()),
            "status": Variant("s", self.status),
            "exit_code": Variant("i", self.exit_code if self.exit_code is not None else -1)
        }
    
    @method()
    def Abort(self) -> None:
        """Abort the running command"""
        if self.process and self.status == "running":
            logger.info(f"Aborting command {self.execution_id}")
            self.aborted = True
            self.process.terminate()
            self.status = "aborted"
    
    @method()
    def GetOutputHistory(self) -> "as":  # type: ignore  # noqa: F722,F821
        """Get the complete output history for this run"""
        return self.output_history
    
    async def execute_async(self) -> None:
        """Execute command asynchronously and stream output"""
        try:
            logger.info(f"Executing command {self.execution_id}: {self.command}")
            
            # Start the process with command as list
            self.process = await asyncio.create_subprocess_exec(
                *self.command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            
            # Read output line by line and emit signals
            try:
                if self.process.stdout:
                    async for line in self.process.stdout:
                        if self.aborted:
                            break
                        self.OutputReceived(line.decode("utf-8"))
            except Exception as e:
                if not self.aborted:
                    logger.error(f"Error reading output: {e}")
            
            # Wait for process completion
            exit_code = await self.process.wait()
            
            # Update status based on whether it was aborted
            if self.aborted:
                self.status = "aborted"
                self.exit_code = 130  # Standard exit code for SIGINT
                logger.info(f"Command {self.execution_id} aborted")
            else:
                self.status = "completed"
                self.exit_code = exit_code
                logger.info(f"Command {self.execution_id} completed with exit code: {exit_code}")
            
            # Signal completion
            self.CommandCompleted(self.exit_code)
            
        except Exception as e:
            error_msg = f"Error executing command '{self.command}': {str(e)}"
            logger.error(error_msg)
            self.status = "error"
            self.exit_code = 1
            self.OutputReceived(f"ERROR: {error_msg}\n")
            self.CommandCompleted(1)


class CommandExecutorInterface(ServiceInterface):
    def __init__(self, bus: MessageBus):
        super().__init__("com.radium226.CommandExecutor")
        self.bus = bus
        self.runs: dict[str, RunInterface] = {}  # Store active run instances
        self.sequence_counter = 0
    
    @method()
    def ExecuteCommand(self, command: "as") -> "s":  # type: ignore  # noqa: F722,F821
        """Execute a command and return the D-Bus path to the Run instance"""
        execution_id = str(uuid.uuid4())
        run_path = f"/com/radium226/Run/{execution_id.replace('-', '_')}"
        
        # Increment sequence counter for this run
        self.sequence_counter += 1
        
        logger.info(f"Starting command execution {execution_id}: {command}")
        
        # Create Run instance with sequence number
        run_instance = RunInterface(execution_id, command, self.sequence_counter)
        self.runs[execution_id] = run_instance
        
        # Export the run instance to D-Bus
        self.bus.export(run_path, run_instance)
        
        # Start the command execution asynchronously
        asyncio.create_task(run_instance.execute_async())
        
        # Return the D-Bus path to the Run instance
        return run_path
    
    @method()
    def ListRuns(self) -> "a{s(sass)}":  # type: ignore  # noqa: F722
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
    def GetRunPath(self, execution_id: "s") -> "s":  # type: ignore  # noqa: F821
        """Get the D-Bus path for a specific run by execution ID"""
        if execution_id not in self.runs:
            raise Exception(f"Run with execution ID {execution_id} not found")
        
        run_path = f"/com/radium226/Run/{execution_id.replace('-', '_')}"
        return run_path
    
    @method()
    def GetLastRunId(self) -> "s":  # type: ignore  # noqa: F821
        """Get the execution ID of the most recent run"""
        if not self.runs:
            raise Exception("No runs found")
        
        # Find the run with the highest sequence number
        last_run = max(self.runs.values(), key=lambda run: run.sequence_number)
        return last_run.execution_id


async def main() -> None:
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
def app() -> None:
    logger.info("Starting D-Bus command executor server...")
    asyncio.run(main())