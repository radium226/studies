# Privilege Escalation Implementation

This document describes the user privilege escalation functionality implemented in the radium226-run D-Bus daemon.

## Overview

The system now supports running the `rund` server as root while executing commands as the original user who invoked the `run` client. This enables privilege escalation scenarios for the D-Bus daemon study.

### Bus Selection Strategy

The system intelligently selects which D-Bus bus to use:

- **System Bus**: When `rund` runs as root (uid 0), it connects to the system bus
- **Session Bus**: When `rund` runs as a regular user, it connects to the session bus
- **Client Auto-Detection**: The `run` client automatically tries the system bus first (to find root servers), then falls back to the session bus

This follows D-Bus best practices and provides proper isolation between system-level and user-level services.

## How It Works

### Client Side (`run` command)
1. The client gets the current user information using `os.getuid()` and `pwd.getpwuid()`
2. User information (username) is sent to the server along with the command
3. Logs show: `Executing command as user: <username> (uid: <uid>)`

### Server Side (`rund` command)
1. **Bus Selection**: Determines which bus to use based on UID:
   - Root (uid 0): Connects to system bus
   - Regular user: Connects to session bus
2. Server receives the command and target user information
3. Checks if it's running as root (`os.getuid() == 0`)
4. If running as root:
   - Looks up the target user's UID and GID using `pwd.getpwnam()`
   - Uses `preexec_fn` with `os.setuid()` and `os.setgid()` to switch to target user
   - Executes the command as the target user
5. If not running as root:
   - Verifies the target user matches the current user
   - Executes the command normally (no switching needed)

### Error Handling
- **Invalid user**: Server returns error if target user doesn't exist on system
- **Permission denied**: Server returns error if trying to switch users without root privileges
- **Execution failures**: Standard error handling for command execution

## Code Changes

### Client (`src/radium226/run/client/app.py`)
```python
# Bus detection logic - tries system bus first, then session bus
async def _connect_to_server_bus() -> tuple[MessageBus, BusType]:
    # Try system bus first (for root servers)
    try:
        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        await bus.introspect("com.radium226.CommandExecutor", "/com/radium226/CommandExecutor")
        return bus, BusType.SYSTEM
    except Exception:
        # Fall back to session bus
        bus = await MessageBus(bus_type=BusType.SESSION).connect()
        await bus.introspect("com.radium226.CommandExecutor", "/com/radium226/CommandExecutor")
        return bus, BusType.SESSION

# Get current user information
current_uid = os.getuid()
current_user = pwd.getpwuid(current_uid).pw_name

# Call ExecuteCommand with user parameter
run_path = await executor_interface.call_execute_command(command, current_user)
```

### Server (`src/radium226/run/server/app.py`)
```python
# Bus selection in main()
current_uid = os.getuid()
if current_uid == 0:
    bus_type = BusType.SYSTEM
    logger.info("Running as root, using system bus")
else:
    bus_type = BusType.SESSION
    logger.info(f"Running as user (uid: {current_uid}), using session bus")

bus = await MessageBus(bus_type=bus_type).connect()

@method()
def ExecuteCommand(self, command: "as", target_user: "s") -> "s":
    # Creates run with target user info
    run_instance = RunInterface(execution_id, command, self.sequence_counter, target_user)

# In execute_async():
def switch_user() -> None:
    if target_gid is not None:
        os.setgid(target_gid)
    if target_uid is not None:
        os.setuid(target_uid)

preexec_fn = switch_user if target_uid is not None else None

self.process = await asyncio.create_subprocess_exec(
    *self.command,
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.STDOUT,
    preexec_fn=preexec_fn,
)
```

## Testing

### Running as Regular User
When running normally (not as root), the system uses the session bus:
```bash
$ uv run rund &          # Server runs as current user on session bus
$ uv run run exec -- whoami   # Client connects to session bus, executes as current user
adrien
```
Logs show: `Running as user (uid: 1000), using session bus` and `Found CommandExecutor service on session bus`

### Running as Root (Privilege Escalation)
When the server runs as root, it uses the system bus and can execute commands as any user:
```bash
$ sudo uv run rund &     # Server runs as root on system bus  
$ uv run run exec -- whoami   # Client finds server on system bus, executes as original user (adrien)
adrien
$ sudo -u bob uv run run exec -- whoami  # Executes as bob
bob
```
Logs show: `Running as root, using system bus` and `Found CommandExecutor service on system bus`

## Security Considerations

1. **Bus Isolation**: System bus for root services, session bus for user services provides proper privilege separation
2. **User Validation**: Server validates that target users exist on the system
3. **Permission Checks**: Server only allows user switching when running as root
4. **Process Isolation**: Each command runs in its own process with proper user context
5. **Error Handling**: Clear error messages for permission and user lookup failures
6. **Service Discovery**: Client auto-detects the appropriate bus, preferring system bus for privilege escalation scenarios

## Test Coverage

The implementation includes comprehensive tests:
- ✅ Normal execution without user switching
- ✅ User information transmission and logging
- ✅ Error handling for invalid scenarios
- ✅ Integration with existing CTRL+C abort functionality
- ✅ Output preservation and attachment features

## Logs Example

### Session Bus (Regular User):
```
INFO | Running as user (uid: 1000), using session bus
INFO | D-Bus server ready and listening for commands on session bus
INFO | Found CommandExecutor service on session bus
INFO | Connected to D-Bus server on session bus
INFO | Executing command as user: adrien (uid: 1000)
INFO | Starting command execution abc123: ['whoami'] as user: adrien
INFO | Server running as adrien, no user switching needed
INFO | Command abc123 completed with exit code: 0
```

### System Bus (Root Server):
```
INFO | Running as root, using system bus
INFO | D-Bus server ready and listening for commands on system bus
INFO | Found CommandExecutor service on system bus
INFO | Connected to D-Bus server on system bus
INFO | Executing command as user: adrien (uid: 1000)  
INFO | Starting command execution def456: ['whoami'] as user: adrien
INFO | Server running as root, switching to user adrien (uid: 1000, gid: 1000)
INFO | Command def456 completed with exit code: 0
```