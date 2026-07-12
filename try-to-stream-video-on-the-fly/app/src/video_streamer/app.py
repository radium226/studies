from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import click
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from video_streamer.broadcaster import Broadcaster, LaggedError
from video_streamer.engine import Engine
from video_streamer.orchestrator import Orchestrator
from video_streamer.reader import Reader, probe_video_info
from video_streamer.sample_asset import ensure_sample_asset
from video_streamer.writer import DEFAULT_FRAG_DURATION_MS, Writer

logging.basicConfig(level=logging.INFO)


class SuppressShutdownCancellation(logging.Filter):
    """Silence the harmless CancelledError/KeyboardInterrupt uvicorn logs as
    "Exception in ASGI application" when Ctrl-C interrupts an open
    /stream.mp4 connection."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.exc_info is None:
            return True
        exc = record.exc_info[1]
        return not isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError))


logging.getLogger("uvicorn.error").addFilter(SuppressShutdownCancellation())

BASE_DIR = Path(__file__).parent
ASSETS_DIR = BASE_DIR.parent.parent / "assets"
SAMPLE_VIDEO = ASSETS_DIR / "sample.mp4"

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@asynccontextmanager
async def lifespan(app: Starlette):
    await ensure_sample_asset(SAMPLE_VIDEO)
    width, height, fps = await probe_video_info(SAMPLE_VIDEO)

    async with (
        Reader.start(SAMPLE_VIDEO) as reader,
        Writer.start(
            width,
            height,
            fps,
            frag_duration_ms=getattr(
                app.state, "frag_duration_ms", DEFAULT_FRAG_DURATION_MS
            ),
        ) as writer,
        Engine.start(
            simulate_input_lag=getattr(app.state, "simulate_input_lag", False)
        ) as engine,
        Broadcaster.start() as broadcaster,
        Orchestrator.start(
            reader, engine, writer, broadcaster, width, height
        ) as orchestrator,
    ):
        app.state.broadcaster = broadcaster
        app.state.orchestrator = orchestrator
        yield


async def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {})


async def stream(request: Request):
    broadcaster: Broadcaster = request.app.state.broadcaster

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
                continue
            yield next_fragment.data
            last_seq = next_fragment.seq

    return StreamingResponse(
        generate(),
        media_type="video/mp4",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


app = Starlette(
    lifespan=lifespan,
    routes=[
        Route("/", index),
        Route("/stream.mp4", stream),
        Mount(
            "/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static"
        ),
    ],
)


@click.command()
@click.option(
    "--simulate-input-lag",
    is_flag=True,
    default=False,
    help="Inject random per-frame delay before encoding, to simulate a laggy input source.",
)
@click.option(
    "--frag-duration-ms",
    type=int,
    default=DEFAULT_FRAG_DURATION_MS,
    show_default=True,
    help="Target fragment duration in milliseconds (ffmpeg -frag_duration, converted to us).",
)
def main(simulate_input_lag: bool, frag_duration_ms: int) -> None:
    app.state.simulate_input_lag = simulate_input_lag
    app.state.frag_duration_ms = frag_duration_ms

    # Bounds how long uvicorn waits for in-flight /stream.mp4 connections on
    # SIGINT/SIGTERM before force-cancelling them; matches wait_for_next's own
    # 5s per-iteration timeout so shutdown isn't needlessly slow.
    config = uvicorn.Config(
        app, host="127.0.0.1", port=8000, timeout_graceful_shutdown=5
    )
    server = uvicorn.Server(config)
    try:
        server.run()
    except KeyboardInterrupt:
        # Server.run() already completed a graceful shutdown by this point -
        # uvicorn deliberately re-raises the original SIGINT/SIGTERM after
        # restoring the default signal handlers (see Server.capture_signals),
        # so callers who want default signal behavior get it. We don't, so we
        # swallow it here; otherwise it reaches click's own KeyboardInterrupt
        # handler, which prints "Aborted!" and exits 1, turning a clean
        # Ctrl-C shutdown into an apparent failure (e.g. mise's `set -e`).
        pass
