# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Python project called "radium226-run" that implements a basic client-server architecture using Click for CLI functionality. The project appears to be a study/experiment for D-Bus daemon privilege escalation, with separate client and server applications.

## Development Commands

### Package Management
- `uv sync` - Install dependencies (uses uv as package manager)
- `uv sync --group dev` - Install development dependencies

### Code Quality
- `uv run ruff check` - Run linting
- `uv run ruff format` - Format code
- `uv run mypy src/` - Run type checking

### Testing
- `uv run pytest` - Run all tests
- `uv run pytest tests/` - Run tests in specific directory
- `uv run pytest -v` - Run tests with verbose output

### Running Applications
- `uv run rund` - Run the server application
- `uv run run` - Run the client application

## Project Structure

```
src/radium226/run/
├── client/
│   ├── __init__.py
│   └── app.py          # Client CLI application
└── server/
    ├── __init__.py
    └── app.py          # Server CLI application
```

## Architecture

The project follows a simple client-server pattern:

- **Server** (`src/radium226/run/server/app.py`): Basic Click-based CLI application that starts a server
- **Client** (`src/radium226/run/client/app.py`): Basic Click-based CLI application that acts as a client
- Both applications use loguru for logging
- Entry points are defined in pyproject.toml as `rund` for server and `run` for client

## Development Configuration

- **Python Version**: Requires Python >=3.12
- **Dependencies**: Click, loguru, PyYAML, requests
- **Dev Dependencies**: mypy, pytest, ruff, types-requests
- **Build System**: Uses hatchling
- **Type Checking**: mypy configured with strict mode enabled
- **Testing**: pytest configured to run tests from `tests/` directory with `--capture=no`

## Package Installation

The project can be installed as a package, providing two console commands:
- `rund` - Runs the server application
- `run` - Runs the client application