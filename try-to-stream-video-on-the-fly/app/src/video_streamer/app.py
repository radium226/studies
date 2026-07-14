from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import click
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from video_streamer.broadcaster import Broadcaster, LaggedError
from video_streamer.engine import Engine
from video_streamer.pipeline import PipelineManager
from video_streamer.writer import DEFAULT_FRAG_DURATION_MS

logging.basicConfig(level=logging.INFO)

logger = logging.getLogger(__name__)


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
MODELS_DIR = BASE_DIR.parent.parent / "models"
SAMPLE_VIDEO = ASSETS_DIR / "sample.mp4"

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@asynccontextmanager
async def lifespan(app: Starlette):
    manager = PipelineManager(app, sample_video=SAMPLE_VIDEO, models_dir=MODELS_DIR)
    app.state.pipeline_manager = manager
    await manager.build_initial()
    try:
        yield
    finally:
        await manager.aclose()


async def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {})


async def metrics(request: Request):
    engine: Engine = request.app.state.engine
    return JSONResponse(engine.metrics_snapshot())


async def stream(request: Request):
    broadcaster: Broadcaster = request.app.state.broadcaster
    if broadcaster.is_closed:
        # Distinguishable from a network error so the player can show "ended"
        # and retry slowly (a source switch may bring up a new broadcaster).
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
    url = (body.get("url") or "").strip()
    if not url:
        return JSONResponse({"error": "url must not be empty"}, status_code=400)
    if not url.startswith(("http://", "https://")):
        return JSONResponse({"error": "url must be http(s)"}, status_code=400)
    manager: PipelineManager = request.app.state.pipeline_manager
    try:
        # The timeout bounds how long a hung yt-dlp/ffprobe can hold the
        # rebuild lock (and this request) hostage.
        async with asyncio.timeout(SOURCE_SWITCH_TIMEOUT_S):
            w, h, fps = await manager.switch_to_url(url)
    except TimeoutError:
        logger.error("source switch to %r timed out after %ss", url, SOURCE_SWITCH_TIMEOUT_S)
        return JSONResponse(
            {"error": f"source switch timed out after {SOURCE_SWITCH_TIMEOUT_S}s"},
            status_code=504,
        )
    except Exception as exc:
        logger.exception("source switch to %r failed", url)
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse({"width": w, "height": h, "fps": fps})


app = Starlette(
    lifespan=lifespan,
    routes=[
        Route("/", index),
        Route("/stream.mp4", stream),
        Route("/metrics", metrics),
        Route("/api/source", set_source, methods=["POST"]),
        Mount(
            "/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static"
        ),
    ],
)


@click.command()
@click.option(
    "--input-video",
    type=click.Choice(["synthetic", "url"]),
    default="synthetic",
    show_default=True,
    help="Input video strategy.",
)
@click.option(
    "--input-video-url",
    default=None,
    help="URL to stream via yt-dlp (required when --input-video=url).",
)
@click.option(
    "--repeat-input-video",
    is_flag=True,
    default=False,
    help="Loop the input video indefinitely.",
)
@click.option(
    "--resize-video",
    default=None,
    metavar="WxH",
    help="Resize frames to WxH before processing (e.g. 1280x720).",
)
@click.option(
    "--speed-factor",
    type=click.FloatRange(min=0.0, min_open=True),
    default=1.0,
    show_default=True,
    help="Playback speed multiplier: scales both the decoder read-rate and the "
    "encoder output fps (e.g. 2 = twice as fast, 0.5 = slow motion). All frames "
    "are still decoded and encoded; the speed-up is pure time-compression.",
)
@click.option(
    "--frag-duration-ms",
    type=int,
    default=DEFAULT_FRAG_DURATION_MS,
    show_default=True,
    help="Target fragment duration in milliseconds (ffmpeg -frag_duration, converted to us).",
)
@click.option(
    "--scrfd-batch-frames",
    type=int,
    default=4,
    show_default=True,
    help="Number of frames per batched SCRFD detection pass (N).",
)
@click.option(
    "--arcface-batch-crops",
    type=int,
    default=8,
    show_default=True,
    help="Max face crops per batched ArcFace embedding pass; larger batches are chunked (M).",
)
def main(
    input_video: str,
    input_video_url: str | None,
    repeat_input_video: bool,
    resize_video: str | None,
    speed_factor: float,
    frag_duration_ms: int,
    scrfd_batch_frames: int,
    arcface_batch_crops: int,
) -> None:
    if input_video == "url" and not input_video_url:
        raise click.UsageError("--input-video-url is required when --input-video=url")

    resize: tuple[int, int] | None = None
    if resize_video:
        try:
            rw, rh = (int(x) for x in resize_video.split("x", 1))
            resize = (rw, rh)
        except ValueError:
            raise click.BadParameter(
                "expected WxH format, e.g. 1280x720", param_hint="'--resize-video'"
            ) from None

    app.state.input_video = input_video
    app.state.input_video_url = input_video_url
    app.state.repeat_input_video = repeat_input_video
    app.state.resize = resize
    app.state.speed_factor = speed_factor
    app.state.frag_duration_ms = frag_duration_ms
    app.state.scrfd_batch_frames = scrfd_batch_frames
    app.state.arcface_batch_crops = arcface_batch_crops

    # Bounds how long uvicorn waits for in-flight /stream.mp4 connections on
    # SIGINT/SIGTERM before force-cancelling them; matches wait_for_next's own
    # 5s per-iteration timeout so shutdown isn't needlessly slow.
    config = uvicorn.Config(
        app, host="127.0.0.1", port=8000, timeout_graceful_shutdown=5
    )
    server = uvicorn.Server(config)
    # Server.run() already completes a graceful shutdown on Ctrl-C - uvicorn
    # deliberately re-raises the original SIGINT/SIGTERM after restoring the
    # default signal handlers (see Server.capture_signals), so callers who
    # want default signal behavior get it. We don't, so we swallow it here;
    # otherwise it reaches click's own KeyboardInterrupt handler, which
    # prints "Aborted!" and exits 1, turning a clean Ctrl-C shutdown into an
    # apparent failure (e.g. mise's `set -e`).
    with contextlib.suppress(KeyboardInterrupt):
        server.run()
