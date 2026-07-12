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
from video_streamer.reader import DEFAULT_FRAG_DURATION_MS, Reader
from video_streamer.sample_asset import ensure_sample_asset

logging.basicConfig(level=logging.INFO)

BASE_DIR = Path(__file__).parent
ASSETS_DIR = BASE_DIR.parent.parent / "assets"
SAMPLE_VIDEO = ASSETS_DIR / "sample.mp4"

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@asynccontextmanager
async def lifespan(app: Starlette):
    ensure_sample_asset(SAMPLE_VIDEO)

    broadcaster = Broadcaster()
    reader = Reader(
        broadcaster,
        SAMPLE_VIDEO,
        simulate_input_lag=getattr(app.state, "simulate_input_lag", False),
        frag_duration_ms=getattr(app.state, "frag_duration_ms", DEFAULT_FRAG_DURATION_MS),
    )
    reader.start()

    app.state.broadcaster = broadcaster
    app.state.reader = reader
    try:
        yield
    finally:
        await asyncio.to_thread(reader.stop)


async def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {})


async def stream(request: Request):
    broadcaster: Broadcaster = request.app.state.broadcaster

    async def generate():
        init_segment, fragment = await asyncio.to_thread(broadcaster.snapshot_for_new_client)
        if init_segment is None:
            await asyncio.sleep(0.5)
            init_segment, fragment = await asyncio.to_thread(broadcaster.snapshot_for_new_client)
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
                next_fragment = await asyncio.to_thread(
                    broadcaster.wait_for_next, last_seq, 5.0
                )
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
        Mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static"),
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
    uvicorn.run(app, host="127.0.0.1", port=8000)
