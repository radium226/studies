from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import click
import uvicorn
from loguru import logger
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from video_analyzer import core, kernel

from . import fs_browser
from .broadcaster import Broadcaster, LaggedError
from .config import WebappConfig
from .pipeline_manager import PipelineManager


class SuppressShutdownCancellation(logging.Filter):
    """Silence the harmless CancelledError/KeyboardInterrupt uvicorn logs as "Exception in ASGI
    application" when Ctrl-C interrupts an open live stream connection."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.exc_info is None:
            return True
        exc = record.exc_info[1]
        return not isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError))


class InterceptHandler(logging.Handler):
    """Redirect stdlib logging records (uvicorn, asyncio, ...) into loguru so everything flows
    through a single sink."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        # Walk back out of the logging machinery so loguru reports the real caller as the log
        # origin.
        frame, depth = logging.currentframe(), 2
        while frame is not None and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1
        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def _configure_logging() -> None:
    """kernel/core/webapp each disable their own logger by default (library etiquette — see
    kernel/CLAUDE.md); as the application entry point, opt back in and print everything, and
    redirect uvicorn's stdlib logging into the same sink (matching app/app.py's own
    configure_logging())."""
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    logger.enable("video_analyzer")
    logging.basicConfig(handlers=[InterceptHandler()], level=logging.INFO, force=True)
    logging.getLogger("uvicorn.error").addFilter(SuppressShutdownCancellation())


BASE_DIR = Path(__file__).parent

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@asynccontextmanager
async def lifespan(app: Starlette):
    # Starts idle: the manager sets up no-source app.state and builds nothing until the UI
    # requests a source.
    manager = PipelineManager(app, config=app.state.config)
    app.state.pipeline_manager = manager
    try:
        yield
    finally:
        await manager.aclose()


async def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {})


async def status(request: Request):
    manager: PipelineManager = request.app.state.pipeline_manager
    return JSONResponse(manager.status())


async def browse(request: Request):
    config: WebappConfig = request.app.state.config
    path = request.query_params.get("path")
    try:
        listing = fs_browser.list_directory(path, default=config.video_library.directory)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse(
        {
            "path": listing.path,
            "parent": listing.parent,
            "entries": [
                {
                    "name": e.name,
                    "path": e.path,
                    "is_dir": e.is_dir,
                    "size": e.size,
                    "modified": e.modified,
                }
                for e in listing.entries
            ],
        }
    )


async def stream(request: Request):
    stream_id = request.path_params["stream_id"]
    broadcaster: Broadcaster | None = request.app.state.broadcaster
    if broadcaster is None or stream_id != request.app.state.stream_id or broadcaster.is_closed:
        # No pipeline (idle), this id belongs to a superseded build, or the current one's
        # broadcaster already closed. Distinguishable from a network error so the player can show
        # "ended" and drop back to its idle poll loop.
        return JSONResponse({"error": "stream ended"}, status_code=410)

    async def generate():
        init_segment, fragment = broadcaster.snapshot_for_new_client()
        if init_segment is None:
            await asyncio.sleep(0.5)
            init_segment, fragment = broadcaster.snapshot_for_new_client()
        if init_segment is None:
            return

        yield init_segment
        last_seq = -1
        if fragment is not None:
            yield fragment.data
            last_seq = fragment.seq

        while True:
            if await request.is_disconnected():
                break
            try:
                next_fragment = await broadcaster.wait_for_next(last_seq, 5.0)
            except LaggedError:
                break
            if next_fragment is None:
                if broadcaster.is_closed:
                    break
                continue
            yield next_fragment.data
            last_seq = next_fragment.seq

    return StreamingResponse(
        generate(),
        media_type="video/mp4",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


SOURCE_SWITCH_TIMEOUT_S = 60


async def set_source(request: Request):
    body = await request.json()
    manager: PipelineManager = request.app.state.pipeline_manager

    path = (body.get("path") or "").strip()
    if not path:
        return JSONResponse({"error": "path must not be empty"}, status_code=400)
    speed_factor_raw = body.get("speed_factor", 1.0)
    try:
        speed_factor = float(speed_factor_raw)
    except (TypeError, ValueError):
        return JSONResponse(
            {"error": f"speed_factor must be a number, got {speed_factor_raw!r}"},
            status_code=400,
        )
    if speed_factor <= 0:
        return JSONResponse(
            {"error": f"speed_factor must be > 0, got {speed_factor}"}, status_code=400
        )
    try:
        # Resolved (and validated) before tearing anything down — a bad path shouldn't cost
        # whatever is currently playing.
        source_path = fs_browser.resolve_video_path(path)
        stop_strategy = core.StopStrategyConfig.from_dict(body.get("stop_strategy"))
    except ValueError as exc:
        # kernel.ConfigError (the stop-strategy validator) subclasses ValueError.
        return JSONResponse({"error": str(exc)}, status_code=400)

    try:
        # The timeout bounds how long a hung ffmpeg/ffprobe can hold the rebuild lock (and this
        # request) hostage.
        async with asyncio.timeout(SOURCE_SWITCH_TIMEOUT_S):
            info = await manager.start(source_path, speed_factor, stop_strategy)
    except TimeoutError:
        logger.error("source start ({!r}) timed out after {}s", path, SOURCE_SWITCH_TIMEOUT_S)
        return JSONResponse(
            {"error": f"source start timed out after {SOURCE_SWITCH_TIMEOUT_S}s"},
            status_code=504,
        )
    except Exception as exc:
        logger.exception("source start ({!r}) failed", path)
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse({"width": info.width, "height": info.height, "fps": info.fps})


async def stop_source(request: Request):
    manager: PipelineManager = request.app.state.pipeline_manager
    await manager.go_idle()
    return JSONResponse({"state": "idle"})


app = Starlette(
    lifespan=lifespan,
    routes=[
        Route("/", index),
        Route("/{stream_id}.mp4", stream),
        Route("/api/status", status),
        Route("/api/browse", browse),
        Route("/api/source", set_source, methods=["POST"]),
        Route("/api/stop", stop_source, methods=["POST"]),
        Mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static"),
    ],
)


@click.command()
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="YAML file holding every static tuning knob (model paths, pipeline batching/lookahead, "
    "frame source/sink settings, the browsable video directory, server host/port). The file to "
    "play and the stop strategy are chosen live from the web UI, not this file. Omit it to run "
    "on defaults. Start one from --dump-config.",
)
@click.option(
    "--dump-config",
    is_flag=True,
    default=False,
    help="Print the full default configuration as YAML and exit — every section and key, "
    "filled in with exactly what a bare run uses. This is the schema reference: redirect it to "
    "a file, edit, and pass it back with --config.",
)
def main(config_path: Path | None, dump_config: bool) -> None:
    _configure_logging()
    if dump_config:
        click.echo(WebappConfig().to_yaml(), nl=False)
        return
    try:
        config = (
            WebappConfig.from_yaml_file(config_path)
            if config_path is not None
            else WebappConfig()
        )
    except kernel.ConfigError as error:
        # A typo in a config file is a user error, not a traceback.
        raise click.BadParameter(str(error), param_hint="--config") from error

    app.state.config = config

    # Bounds how long uvicorn waits for in-flight live stream connections on SIGINT/SIGTERM
    # before force-cancelling them; matches wait_for_next's own 5s per-iteration timeout so
    # shutdown isn't needlessly slow. log_config=None keeps uvicorn from installing its own
    # stderr handlers, so its records propagate to the root InterceptHandler and flow through
    # loguru.
    server_config = uvicorn.Config(
        app,
        host=config.server.host,
        port=config.server.port,
        timeout_graceful_shutdown=5,
        log_config=None,
    )
    server = uvicorn.Server(server_config)
    # Server.run() already completes a graceful shutdown on Ctrl-C - uvicorn deliberately
    # re-raises the original SIGINT/SIGTERM after restoring the default signal handlers (see
    # Server.capture_signals), so callers who want default signal behavior get it. We don't, so
    # we swallow it here; otherwise it reaches click's own KeyboardInterrupt handler, which
    # prints "Aborted!" and exits 1, turning a clean Ctrl-C shutdown into an apparent failure
    # (e.g. mise's `set -e`).
    with contextlib.suppress(KeyboardInterrupt):
        server.run()


if __name__ == "__main__":
    main()
