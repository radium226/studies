# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv sync                        # Install/sync dependencies
uv run radium226-studies-ipc   # Run the CLI entry point
uv run ty check                # Type-check with ty (dev dependency)
uv build                       # Build the package
```

### CLI usage

```bash
# Terminal 1 — start the server
uv run radium226-studies-ipc start-server

# Terminal 2 — send a request
uv run radium226-studies-ipc hello Alice
```

## Architecture

This is a Python study/exploration project for IPC over Unix domain sockets, using **uv** for package management and Python 3.13+.

**Namespace package layout** — source lives under `src/radium226/studies/ipc/`. The `radium226/` and `radium226/studies/` directories are namespace packages (no `__init__.py`); only `ipc/` has one.

The CLI entry point (`radium226-studies-ipc`) maps to `radium226.studies.ipc:app` (a Click group).

### Layer stack (bottom to top)

| Layer | File | Responsibility |
|---|---|---|
| Transport | `transport.py` | Raw Unix socket I/O, framing, SCM_RIGHTS fd passing |
| Protocol | `protocol.py` | `Request` base class, `Response` protocol, `Codec`, `ResponseHandler` |
| Server / Client | `server.py`, `client.py` | Async request/response + event multiplexing over a `Connection` |
| IPC facade | `ipc.py` | `open_server` / `open_client` module-level async context managers |
| CLI | `cli/app.py` | Click commands wiring messages to the IPC facade |
| Messages | `cli/messages.py` | Pydantic models for the demo protocol |

### Key design points

- **Framing**: `Framing` is a structural protocol (`delimit` / `extract`). The default `NullCharFraming` delimits frames with `\x00`. Frames also carry optional file descriptors via `SCM_RIGHTS`.
- **Codec**: A `Codec[RequestT, EventT, ResponseT]` bundles `encode` and `decode` callables. The CLI uses a Pydantic `TypeAdapter` over the message union with a `type` literal discriminator field on each model.
- **Request base class**: `Request[ResponseT, EventT]` is a generic base class (not a structural protocol). Subclasses inherit from it with concrete type args (e.g., `Hello(BaseModel, Request[World, Foo | Bar])`), and `__init_subclass__` extracts `__response_type__` / `__event_type__` for runtime validation via `validate_response` / `validate_event`.
- **Response protocol**: `Response` is a structural protocol — any object with `request_id: str`. The client correlates responses to pending requests by `request_id`.
- **Events**: The server can push intermediate events before sending the final response via the `emit` callback passed to the handler. The client routes events to all pending requests' `on_event` handlers.
- **Generics**: `Server`, `Client`, `Codec`, and the `open_server`/`open_client` functions are all generic over `(RequestT, EventT, ResponseT)` using Python 3.12+ type parameter syntax.
