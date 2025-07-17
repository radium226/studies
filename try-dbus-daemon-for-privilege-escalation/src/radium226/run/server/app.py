import asyncio
import os
import pwd
import uuid
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Optional
from click import command
from loguru import logger

from dbus_fast.aio import MessageBus
from dbus_fast import BusType, Variant
from dbus_fast.service import ServiceInterface, method, signal


@dataclass
class CleanupConfig:
    """Configuration for run cleanup policies"""
    max_age_hours: int = 24  # Remove runs older than this many hours
    max_completed_runs: int = 100  # Keep at most this many completed runs
    max_total_runs: int = 500  # Keep at most this many total runs
    cleanup_interval_minutes: int = 60  # Run cleanup every N minutes
    keep_running: bool = True  # Never cleanup running processes


class RunCleanupService:
    """Service for cleaning up old run instances"""
    
    def __init__(self, config: CleanupConfig):
        self.config = config
        self._cleanup_task: Optional[asyncio.Task] = None
        
    def start_automatic_cleanup(self, runs_dict: dict, bus: MessageBus):
        """Start automatic cleanup task"""
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(
                self._automatic_cleanup_loop(runs_dict, bus)
            )
            logger.info(f"Started automatic cleanup task (interval: {self.config.cleanup_interval_minutes}m)")
    
    def stop_automatic_cleanup(self):
        """Stop automatic cleanup task"""
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            logger.info("Stopped automatic cleanup task")
    
    async def _automatic_cleanup_loop(self, runs_dict: dict, bus: MessageBus):
        """Automatic cleanup loop"""
        while True:
            try:
                await asyncio.sleep(self.config.cleanup_interval_minutes * 60)
                cleaned_count = self.cleanup_runs(runs_dict, bus)
                if cleaned_count > 0:
                    logger.info(f"Automatic cleanup removed {cleaned_count} old runs")
            except asyncio.CancelledError:
                logger.debug("Cleanup task cancelled")
                break
            except Exception as e:
                logger.error(f"Error in automatic cleanup: {e}")
                await asyncio.sleep(60)  # Wait 1 minute before retrying
    
    def cleanup_runs(self, runs_dict: dict, bus: MessageBus) -> int:
        """
        Clean up old runs based on configured policies.
        Returns the number of runs cleaned up.
        """
        if not runs_dict:
            return 0
            
        runs_to_remove = []
        now = datetime.now()
        
        # Get runs sorted by age (oldest first)
        sorted_runs = sorted(
            runs_dict.items(),
            key=lambda x: x[1].start_time
        )
        
        # Apply cleanup policies
        for execution_id, run in sorted_runs:
            should_remove = False
            
            # Never remove running processes if configured
            if self.config.keep_running and run.status == "running":
                continue
                
            # Age-based cleanup
            age = now - run.start_time
            if age > timedelta(hours=self.config.max_age_hours):
                should_remove = True
                logger.debug(f"Marking run {execution_id} for age cleanup (age: {age})")
            
            if should_remove:
                runs_to_remove.append(execution_id)
        
        # Count-based cleanup for completed runs
        completed_runs = [
            (eid, run) for eid, run in sorted_runs 
            if run.status in ["completed", "aborted", "error"]
        ]
        
        if len(completed_runs) > self.config.max_completed_runs:
            excess_completed = len(completed_runs) - self.config.max_completed_runs
            for i in range(excess_completed):
                execution_id = completed_runs[i][0]
                if execution_id not in runs_to_remove:
                    runs_to_remove.append(execution_id)
                    logger.debug(f"Marking run {execution_id} for completed count cleanup")
        
        # Total count-based cleanup
        if len(sorted_runs) > self.config.max_total_runs:
            excess_total = len(sorted_runs) - self.config.max_total_runs
            for i in range(excess_total):
                execution_id = sorted_runs[i][0]
                if execution_id not in runs_to_remove:
                    # Only remove if not running (if keep_running is True)
                    if not self.config.keep_running or sorted_runs[i][1].status != "running":
                        runs_to_remove.append(execution_id)
                        logger.debug(f"Marking run {execution_id} for total count cleanup")
        
        # Remove the marked runs
        removed_count = 0
        for execution_id in runs_to_remove:
            if execution_id in runs_dict:
                run = runs_dict[execution_id]
                
                # Unexport from D-Bus
                run_path = f"/com/radium226/Run/{execution_id.replace('-', '_')}"
                try:
                    bus.unexport(run_path)
                except Exception as e:
                    logger.warning(f"Failed to unexport run {execution_id}: {e}")
                
                # Remove from dictionary
                del runs_dict[execution_id]
                removed_count += 1
                
                logger.debug(f"Cleaned up run {execution_id} (status: {run.status}, age: {now - run.start_time})")
        
        return removed_count


class RunInterface(ServiceInterface):
    def __init__(self, execution_id: str, command: list[str], sequence_number: int, target_user: str):
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
        self.target_user = target_user
    
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
    
    @method()
    def DoRun(self) -> None:
        """Start the actual command execution"""
        if self.status == "running":
            # Start the command execution asynchronously
            asyncio.create_task(self.execute_async())
    
    async def execute_async(self) -> None:
        """Execute command asynchronously and stream output"""
        try:
            logger.info(f"Executing command {self.execution_id}: {self.command} as user: {self.target_user}")
            
            # Determine if we need to switch users
            current_uid = os.getuid()
            target_uid = None
            target_gid = None
            
            if current_uid == 0:  # Running as root
                try:
                    # Get target user information
                    target_user_info = pwd.getpwnam(self.target_user)
                    target_uid = target_user_info.pw_uid
                    target_gid = target_user_info.pw_gid
                    logger.info(f"Server running as root, switching to user {self.target_user} (uid: {target_uid}, gid: {target_gid})")
                except KeyError:
                    raise Exception(f"User '{self.target_user}' not found on system")
            else:
                # Not running as root, verify we're not trying to switch to a different user
                current_user = pwd.getpwuid(current_uid).pw_name
                if current_user != self.target_user:
                    raise Exception(f"Cannot switch from user '{current_user}' to '{self.target_user}' - server not running as root")
                logger.info(f"Server running as {current_user}, no user switching needed")
            
            # Prepare preexec_fn for user switching if needed
            def switch_user() -> None:
                if target_gid is not None:
                    os.setgid(target_gid)
                if target_uid is not None:
                    os.setuid(target_uid)
            
            preexec_fn = switch_user if target_uid is not None else None
            
            # Start the process with command as list
            self.process = await asyncio.create_subprocess_exec(
                *self.command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                preexec_fn=preexec_fn,
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
            if self.aborted:
                # If aborted, give the process a short time to exit, then kill it
                try:
                    exit_code = await asyncio.wait_for(self.process.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    logger.warning(f"Process {self.execution_id} did not exit after terminate, killing it")
                    self.process.kill()
                    exit_code = await self.process.wait()
            else:
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
            # Check if this is a user switching error
            if "not found on system" in str(e) or "Cannot switch from user" in str(e):
                error_msg = f"User privilege error: {str(e)}"
                logger.error(error_msg)
                self.status = "error"
                self.exit_code = 1
                self.OutputReceived(f"ERROR: {error_msg}\n")
                self.CommandCompleted(1)
            else:
                error_msg = f"Error executing command '{self.command}': {str(e)}"
                logger.error(error_msg)
                self.status = "error"
                self.exit_code = 1
                self.OutputReceived(f"ERROR: {error_msg}\n")
                self.CommandCompleted(1)


class CommandExecutorInterface(ServiceInterface):
    def __init__(self, bus: MessageBus, cleanup_config: Optional[CleanupConfig] = None):
        super().__init__("com.radium226.CommandExecutor")
        self.bus = bus
        self.runs: dict[str, RunInterface] = {}  # Store active run instances
        self.sequence_counter = 0
        
        # Initialize cleanup service
        self.cleanup_config = cleanup_config or CleanupConfig()
        self.cleanup_service = RunCleanupService(self.cleanup_config)
        
        # Start automatic cleanup
        self.cleanup_service.start_automatic_cleanup(self.runs, self.bus)
    
    @method()
    def ExecuteCommand(self, command: "as", target_user: "s") -> "s":  # type: ignore  # noqa: F722,F821
        """Execute a command and return the D-Bus path to the Run instance"""
        execution_id = str(uuid.uuid4())
        run_path = f"/com/radium226/Run/{execution_id.replace('-', '_')}"
        
        # Increment sequence counter for this run
        self.sequence_counter += 1
        
        logger.info(f"Starting command execution {execution_id}: {command} as user: {target_user}")
        
        # Create Run instance with sequence number and target user
        run_instance = RunInterface(execution_id, command, self.sequence_counter, target_user)
        self.runs[execution_id] = run_instance
        
        # Export the run instance to D-Bus
        self.bus.export(run_path, run_instance)
        
        # Return the D-Bus path to the Run instance
        # Note: execution will start when DoRun() is called
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
    
    @method()
    def CleanupOldRuns(self) -> "i":  # type: ignore  # noqa: F821
        """
        Manually trigger cleanup of old runs based on configured policies.
        Returns the number of runs cleaned up.
        """
        cleaned_count = self.cleanup_service.cleanup_runs(self.runs, self.bus)
        logger.info(f"Manual cleanup removed {cleaned_count} old runs")
        return cleaned_count
    
    @method()
    def GetCleanupStats(self) -> "a{sv}":  # type: ignore  # noqa: F722
        """Get statistics about runs and cleanup configuration"""
        now = datetime.now()
        
        # Count runs by status
        status_counts = {}
        oldest_run_age_hours = 0
        
        if self.runs:
            for run in self.runs.values():
                status = run.status
                status_counts[status] = status_counts.get(status, 0) + 1
            
            # Find oldest run
            oldest_run = min(self.runs.values(), key=lambda r: r.start_time)
            oldest_run_age_hours = (now - oldest_run.start_time).total_seconds() / 3600
        
        return {
            "total_runs": Variant("i", len(self.runs)),
            "running_runs": Variant("i", status_counts.get("running", 0)),
            "completed_runs": Variant("i", status_counts.get("completed", 0)),
            "aborted_runs": Variant("i", status_counts.get("aborted", 0)),
            "error_runs": Variant("i", status_counts.get("error", 0)),
            "oldest_run_age_hours": Variant("d", oldest_run_age_hours),
            "max_age_hours": Variant("i", self.cleanup_config.max_age_hours),
            "max_completed_runs": Variant("i", self.cleanup_config.max_completed_runs),
            "max_total_runs": Variant("i", self.cleanup_config.max_total_runs),
            "cleanup_interval_minutes": Variant("i", self.cleanup_config.cleanup_interval_minutes),
            "keep_running": Variant("b", self.cleanup_config.keep_running),
        }
    
    @method()
    def RemoveRun(self, execution_id: "s") -> "b":  # type: ignore  # noqa: F821
        """
        Manually remove a specific run by execution ID.
        Returns True if the run was removed, False if not found.
        """
        if execution_id not in self.runs:
            logger.warning(f"Attempted to remove non-existent run: {execution_id}")
            return False
        
        run = self.runs[execution_id]
        
        # Don't remove running processes (safety check)
        if run.status == "running":
            logger.warning(f"Attempted to remove running process: {execution_id}")
            return False
        
        # Unexport from D-Bus
        run_path = f"/com/radium226/Run/{execution_id.replace('-', '_')}"
        try:
            self.bus.unexport(run_path)
        except Exception as e:
            logger.warning(f"Failed to unexport run {execution_id}: {e}")
        
        # Remove from dictionary
        del self.runs[execution_id]
        logger.info(f"Manually removed run {execution_id}")
        return True


async def main() -> None:
    logger.info("Starting D-Bus server...")
    
    # Determine which bus to use based on whether we're running as root
    current_uid = os.getuid()
    if current_uid == 0:
        bus_type = BusType.SYSTEM
        bus_name = "com.radium226.CommandExecutor"
        logger.info("Running as root, using system bus")
    else:
        bus_type = BusType.SESSION
        bus_name = "com.radium226.CommandExecutor"
        logger.info(f"Running as user (uid: {current_uid}), using session bus")
    
    # Connect to the appropriate bus
    bus = await MessageBus(bus_type=bus_type).connect()
    
    # Create and export the service interface
    interface = CommandExecutorInterface(bus)
    bus.export("/com/radium226/CommandExecutor", interface)
    
    # Request the bus name
    await bus.request_name(bus_name)
    
    logger.info(f"D-Bus server ready and listening for commands on {bus_type.name.lower()} bus")
    
    # Keep the server running

    future: asyncio.Future[None] = asyncio.Future()
    try:
        await future # This will keep the server running until cancelled
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Shutting down D-Bus server...")
        bus.disconnect()
    finally:
        logger.info("D-Bus server stopped")


@command()
def app() -> None:
    logger.info("Starting D-Bus command executor server...")
    asyncio.run(main())