# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Python project that implements a D-Bus based command executor system with a daemon-client architecture. The project uses `uv` for dependency management and provides two main commands:

- `executord`: The daemon that runs as a D-Bus service and executes commands
- `executor`: The client that connects to the daemon to execute commands

## Development Commands

### Package Management
- Install dependencies: `uv sync --dev`
- Install project in development mode: `uv pip install -e .`

### Code Quality
- Run type checking: `uv run mypy src/`
- Run linting and formatting: `uv run ruff check src/`
- Auto-fix linting issues: `uv run ruff check --fix src/`
- Format code: `uv run ruff format src/`

### Testing
- Run tests: `uv run pytest`
- Run tests with verbose output: `uv run pytest -v`

### Running the Application
- Start the daemon: `uv run executord` (or use the installed script `executord`)
- Execute commands via client: `uv run executor <command>` (or use the installed script `executor <command>`)

## Architecture

### Core Components

1. **Daemon (`radium226.executor.daemon`)**:
   - `Executor`: Main daemon class that manages subprocess execution
   - `ExecutorInterface`: D-Bus interface for the executor service
   - Exports interface at D-Bus name `radium226.Executor` and path `/radium226/Executor`

2. **Client (`radium226.executor.client`)**:
   - `Executor`: Client proxy that connects to the daemon via D-Bus
   - Handles command execution requests and signal forwarding (SIGINT/SIGTERM)

3. **Shared D-Bus Components (`radium226.executor.shared.dbus`)**:
   - `ExecutorInterface`: D-Bus interface definition for executor operations
   - `ExecutionInterface`: D-Bus interface for individual command executions
   - `open_bus()`: Utility for opening D-Bus connections

### Execution Flow

1. Client calls `executor <command>` which connects to the daemon via D-Bus
2. Daemon receives execution request and spawns subprocess
3. Daemon creates an `ExecutionInterface` at path `/radium226/Executor/Execution/{pid}`
4. Client monitors execution status and exit code via D-Bus properties
5. Signal handling (SIGINT/SIGTERM) is forwarded from client to daemon to subprocess

### Key Files

- `src/radium226/executor/daemon/domain/executor.py`: Core daemon execution logic
- `src/radium226/executor/client/domain/executor.py`: Client connection and execution monitoring
- `src/radium226/executor/shared/dbus/executor_interface.py`: Main D-Bus interface definition
- `src/radium226/executor/shared/dbus/execution_interface.py`: Per-execution D-Bus interface

## Project Structure

The project follows a domain-driven design pattern with clear separation between client, daemon, and shared components. The D-Bus interfaces provide the communication layer between the client and daemon processes.