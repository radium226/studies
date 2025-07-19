# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a D-Bus daemon system for privilege escalation studies called `radium226-run`. It consists of:

- **Daemon (`rund`)**: A D-Bus service running as root that can execute commands
- **Client (`run`)**: A client application that connects to the daemon to execute commands
- **D-Bus Integration**: Uses the system message bus for communication with proper security policies

⚠️ **Security Research Project**: This tool is designed for security research and privilege escalation studies. Do not use in production environments.

## Development Commands

### Build and Package Management
```bash
# Install development dependencies
uv sync

# Build source distribution
uv build --sdist

# Install package locally for testing
sudo uv tool install --from . rund
sudo uv tool install --from . run

# Build and install via PKGBUILD (Arch Linux)
make install

# Uninstall package
make uninstall
```

### Testing and Quality
```bash
# Run tests
pytest

# Type checking
mypy

# Linting and formatting
ruff check
ruff format
```

### Installation and Service Management
```bash
# Manual installation of D-Bus configuration
sudo cp dbus-config/system-services/com.radium226.CommandExecutor.service /usr/share/dbus-1/system-services/
sudo cp dbus-config/system.d/com.radium226.CommandExecutor.conf /etc/dbus-1/system.d/
sudo systemctl reload dbus

# Install and start systemd service
sudo cp dbus-config/radium226-command-executor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable radium226-command-executor.service
sudo systemctl start radium226-command-executor.service

# Check service status
sudo systemctl status radium226-command-executor.service
```

## Architecture

### Core Components

**Daemon Architecture** (`src/radium226/run/daemon/`):
- `runner_manager.py`: Main RunnerManager class managing command runners and cleanup loops
- `runner.py`: Runner class handling individual command execution with async process management
- `types.py`: Core data types (RunnerManagerConfig)
- `dbus/`: D-Bus interface implementations for the daemon and runner objects

**Client Architecture** (`src/radium226/run/client/`):
- `core.py`: Client class and RunControl for D-Bus communication with the daemon
- `dbus.py`: D-Bus client interface implementation
- `cli.py`: Click-based command line interface

**Shared Types** (`src/radium226/run/shared/`):
- `types.py`: Common types used by both client and daemon (Command, ExitCode, Signal, RunnerID, RunnerStatus)
- `dbus.py`: Shared D-Bus utilities and constants

### D-Bus Communication Flow

1. Client calls `PrepareRunner` on daemon to create a runner for a command
2. Daemon returns a D-Bus object path for the runner
3. Client subscribes to signals from the runner (StdOut, StdErr, Completed)
4. Client calls `Run` on the runner to start execution
5. Runner streams output via D-Bus signals
6. Runner sends `Completed` signal with exit code when finished

### Key Data Types

- `Command`: List of strings representing command and arguments
- `Runner`: Manages individual command execution with ID, status, and command
- `RunnerStatus`: Enum (PREPARED, RUNNING, COMPLETED, FAILED)
- `RunnerManagerConfig`: Configuration including cleanup intervals and run handlers

## Configuration Files

### D-Bus Configuration
- `dbus-config/system-services/com.radium226.CommandExecutor.service`: Service registration
- `dbus-config/system.d/com.radium226.CommandExecutor.conf`: Security policy allowing any user to send messages
- `dbus-config/radium226-command-executor.service`: Systemd service file

### Project Configuration
- `pyproject.toml`: Python project configuration with uv build system
- Scripts: `rund` (server) and `run` (client) entry points
- Dependencies: dbus-fast, click, loguru, pydantic, pyyaml

## Testing and Verification

```bash
# Test basic functionality after installation
run exec -- whoami

# Check D-Bus service registration
dbus-send --system --print-reply --dest=org.freedesktop.DBus /org/freedesktop/DBus org.freedesktop.DBus.ListNames | grep radium226

# Test D-Bus introspection
busctl --system introspect com.radium226.CommandExecutor /com/radium226/CommandExecutor
```

## TODO Items (from README.md)

- Handle `root` using the system message bus
- Propagate environment variables  
- Propagate current working directory
- Fix types, format & unit tests
- Add list and attach commands
- Add netns switch
- Add Jinja variables for IP, ports, etc. related to netns
- Rename the `server` package to `daemon` package
- Rename the `Server` class to `Manager`