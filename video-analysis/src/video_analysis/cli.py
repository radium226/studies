from click import command, argument
from asyncio import run
from pathlib import Path
from asyncio import IncompleteReadError
from asyncio.subprocess import create_subprocess_exec, Process, PIPE
from contextlib import asynccontextmanager, AsyncExitStack
from dataclasses import dataclass
import numpy as np
from numpy.typing import NDArray
from typing import AsyncGenerator
from loguru import logger


type Width = int

type Height = int

@dataclass(frozen=True, slots=True)
class Size():
    width: Width
    height: Height
    fps: float


type Frame = NDArray[np.uint8] | None


async def probe_video(video_file_path: Path) -> Size:
    ffprobe_process = await create_subprocess_exec(
        "ffprobe",
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate",
        "-of", "csv=s=x:p=0",
        str(video_file_path),
        stdout=PIPE,
        stderr=PIPE,
    )

    stdout, stderr = await ffprobe_process.communicate()
    if ffprobe_process.returncode != 0:
        raise RuntimeError(f"ffprobe failed with error: {stderr.decode()}")

    width_str, height_str, fps_str = stdout.decode().strip().split("x")
    fps_num, fps_den = map(int, fps_str.split("/"))
    return Size(width=int(width_str), height=int(height_str), fps=fps_num / fps_den)


async def read_video(video_file_path: Path, video_size: Size) -> AsyncGenerator[Frame, None]:
    ffmpeg_process = await create_subprocess_exec(
        "ffmpeg",
        "-i", str(video_file_path),
        "-f", "rawvideo",
        "-pix_fmt", "rgb24",
        "-",
        stdout=PIPE,
        stderr=PIPE,
    )

    assert ffmpeg_process.stdout is not None

    try:
        while True:
            try:
                frame_data = await ffmpeg_process.stdout.readexactly(3 * video_size.width * video_size.height)
                if not frame_data:
                    break
                yield np.frombuffer(frame_data, dtype=np.uint8).reshape((video_size.height, video_size.width, 3))
            except IncompleteReadError:
                logger.debug("End of video stream reached")
                yield None # Last frame !
                break
    finally:
        logger.debug("Terminating ffmpeg process")
        ffmpeg_process.kill()
        await ffmpeg_process.wait()


@dataclass(frozen=True, slots=True)
class SceneEvent():
    index: int


type Event = SceneEvent


@asynccontextmanager
async def scene_detector(threshold: float = 30.0):
    scene_index = 0
    previous_frame: Frame = None

    async def process(frame: Frame) -> AsyncGenerator[SceneEvent, None]:
        nonlocal scene_index, previous_frame
        if previous_frame is None:
            yield SceneEvent(index=scene_index)
        else:
            if frame is not None:
                diff = np.abs(frame.astype(np.int16) - previous_frame.astype(np.int16))
                if np.mean(diff) > threshold:
                    scene_index += 1
                    yield SceneEvent(index=scene_index)
        previous_frame = frame
    yield process


@asynccontextmanager
async def scene_writer(video_size: Size, output_dir: Path = Path(".")):
    current_process: Process | None = None

    async def start_scene(scene_index: int) -> None:
        nonlocal current_process
        if current_process is not None:
            assert current_process.stdin is not None
            current_process.stdin.close()
            await current_process.wait()

        output_path = output_dir / f"scene-{scene_index:03d}.mp4"
        logger.info("Starting scene {index} -> {path}", index=scene_index, path=output_path)
        current_process = await create_subprocess_exec(
            "ffmpeg",
            "-y",
            "-f", "rawvideo",
            "-pix_fmt", "rgb24",
            "-framerate", str(video_size.fps),
            "-video_size", f"{video_size.width}x{video_size.height}",
            "-i", "pipe:0",
            "-c:v", "libx264",
            "-loglevel", "error",
            str(output_path),
            stdin=PIPE,
            stderr=PIPE,
        )

    async def process(frame: Frame, events: list[Event]) -> None:
        for event in events:
            if isinstance(event, SceneEvent):
                await start_scene(event.index)

        if frame is not None and current_process is not None:
            assert current_process.stdin is not None
            current_process.stdin.write(frame.tobytes())
            await current_process.stdin.drain()

    try:
        yield process
    finally:
        if current_process is not None:
            assert current_process.stdin is not None
            current_process.stdin.close()
            await current_process.wait()


@command()
@argument("video_file_path", type=Path)
def app(video_file_path: Path) -> None:
    async def coroutine() -> None:
        video_size = await probe_video(video_file_path)
        async with AsyncExitStack() as stack:
            detect = await stack.enter_async_context(scene_detector())
            write = await stack.enter_async_context(scene_writer(video_size))
            frame_index = 0
            async for frame in read_video(video_file_path, video_size):
                logger.debug("Processing frame {frame_index}", frame_index=frame_index)
                events: list[Event] = []
                async for event in detect(frame):
                    logger.info("Detected scene event: {event}", event=event)
                    events.append(event)
                await write(frame, events)
                frame_index += 1


    run(coroutine())
