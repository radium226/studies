# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Python-based D-Bus study project called "ech0" that demonstrates Unix file descriptor (FD) passing over D-Bus. The project implements a service that receives file descriptors and reads data from them, showcasing inter-process communication with file descriptor sharing between processes.

## Architecture

The project follows a standard Python package structure with a D-Bus service implementation:

- **Package**: `ech0` - Main package containing daemon and client implementations
- **Service Layer**: `ech0.daemon.service.Ech0Service` - Manages D-Bus service lifecycle and connection
- **Interface Layer**: `ech0.daemon.service.Ech0Interface` - Defines D-Bus methods and properties using dbus-fast
- **CLI Layer**: `ech0.daemon.cli` - Command-line interface for running the daemon
- **Client Layer**: `ech0.client.service.Ech0Client` - D-Bus client for calling the echo service
- **Client CLI**: `ech0.client.cli` - Command-line interface for the client
- **Entry Points**: `ech0d` (daemon) and `ech0` (client) scripts defined in pyproject.toml

### Key Components

- **Ech0Service**: Main service manager that handles D-Bus connection with Unix FD negotiation enabled
- **Ech0Interface**: D-Bus interface with `Echo` method that accepts Unix file descriptors (`"h"` type) and asynchronously reads from them
- **Ech0Client**: D-Bus client that creates pipe pairs and passes read file descriptors to the service
- **Bus Configuration**: Uses SESSION bus with `negotiate_unix_fd=True` for FD passing support
- **FD Communication**: Service reads data from received FDs in background tasks while client writes to pipe

## Development Commands

### Dependencies and Environment
- **Package Manager**: Uses `uv` for dependency management (uv.lock present)
- **Install dependencies**: `uv sync` or `uv install`
- **Install dev dependencies**: `uv sync --group dev`

### Testing and Quality
- **Run tests**: `uv run pytest`
- **Type checking**: `uv run mypy src/`
- **Linting**: `uv run ruff check`
- **Format code**: `uv run ruff format`

### Running the Service and Client
- **Start daemon**: `uv run ech0d` or `python -m ech0.daemon`
- **Use client**: `uv run ech0 "message"` or `python -m ech0.client "message"` 
- **Note**: Client creates a pipe, passes read FD to service, then continuously writes data to write FD
- **Test D-Bus service**: Direct D-Bus testing requires creating file descriptors manually

### Testing File Descriptor Passing
Once the daemon is running:
```bash
# Using the ech0 client (recommended) - demonstrates FD passing
uv run ech0 "test message"

# Direct D-Bus testing with FD passing is complex and requires:
# 1. Creating file descriptors 
# 2. Using dbus-send or busctl with --fd-in options (if available)
# 3. The service expects Unix FD type "h", not string type "s"

# Example conceptual busctl call (actual FD creation not shown):
# busctl --user call radium226.ech0 /ech0 ech0.Ech0 Echo h [fd_number]
```

### Implementation Details
- **FD Type Signature**: Service `Echo` method uses `"h"` (Unix file descriptor) parameter type
- **Async I/O**: Service uses `StreamReader` and `StreamWriter` with `connect_read_pipe`/`connect_write_pipe` for non-blocking FD operations
- **Background Processing**: Service spawns background tasks to read from received FDs until EOF and transform data (convert to uppercase)
- **Pipe Communication**: Client creates `os.pipe()`, passes read end to service, writes to write end
- **FD Negotiation**: Both client and service enable `negotiate_unix_fd=True` on MessageBus

## Dependencies

- **Runtime**: `dbus-fast` for D-Bus communication
- **Development**: `mypy`, `pytest`, `ruff` for type checking, testing, and linting
- **Python Version**: Requires Python 3.13+