from click import command, argument
from asyncio import run, get_event_loop
from math import hypot
from pathlib import Path
from asyncio import IncompleteReadError
from asyncio.subprocess import create_subprocess_exec, Process, PIPE
from contextlib import asynccontextmanager, AsyncExitStack
from dataclasses import dataclass
import numpy as np
from numpy.typing import NDArray
from typing import AsyncGenerator
from loguru import logger
import pytesseract
from PIL import Image


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


@dataclass(frozen=True, slots=True)
class Rectangle():
    cx: int
    cy: int
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class TextEvent():
    text: str
    area: Rectangle


type Event = SceneEvent | TextEvent


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
async def text_detector(min_confidence: int = 60):
    loop = get_event_loop()

    async def process(frame: Frame) -> AsyncGenerator[TextEvent, None]:
        if frame is None:
            return

        try:
            image = Image.fromarray(frame)
            data = await loop.run_in_executor(
                None,
                lambda: pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT),
            )

            entries = [
                (word.strip(), data["left"][i], data["top"][i], data["width"][i], data["height"][i])
                for i, (word, conf) in enumerate(zip(data["text"], data["conf"]))
                if str(conf).lstrip("-").isdigit() and int(conf) >= min_confidence and word.strip()
            ]

            if entries:
                words, lefts, tops, widths, heights = zip(*entries)
                x1 = min(lefts)
                y1 = min(tops)
                x2 = max(l + w for l, w in zip(lefts, widths))
                y2 = max(t + h for t, h in zip(tops, heights))
                area = Rectangle(
                    cx=(x1 + x2) // 2,
                    cy=(y1 + y2) // 2,
                    width=x2 - x1,
                    height=y2 - y1,
                )
                yield TextEvent(text=" ".join(words), area=area)
        except Exception:
            logger.exception("Text detection failed")

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


@asynccontextmanager
async def text_writer(
    video_size: Size,
    output_dir: Path = Path("."),
    max_distance: float = 500000.0,
    max_no_text_frames: int = 10,
    min_text_frames: int = 10,
):
    current_process: Process | None = None
    current_output_path: Path | None = None
    text_frame_count: int = 0
    text_index = 0
    crop_size: tuple[int, int] | None = None        # (w, h) fixed for current clip
    current_crop: tuple[int, int, int, int] | None = None  # (x1, y1, x2, y2) updated each frame
    previous_area: Rectangle | None = None
    no_text_frames: int = 0

    async def close_clip() -> None:
        nonlocal current_process, crop_size, current_crop, current_output_path
        if current_process is not None:
            assert current_process.stdin is not None
            current_process.stdin.close()
            await current_process.wait()
            current_process = None
            if text_frame_count < min_text_frames and current_output_path is not None:
                current_output_path.unlink(missing_ok=True)
                logger.info("Discarded {path} (only {n} text frames)", path=current_output_path, n=text_frame_count)
        crop_size = None
        current_crop = None
        current_output_path = None

    async def open_clip(area: Rectangle) -> None:
        nonlocal current_process, text_index, crop_size, current_crop, current_output_path, text_frame_count

        x1 = max(0, area.cx - area.width // 2)
        y1 = max(0, area.cy - area.height // 2)
        x2 = min(video_size.width, x1 + area.width)
        y2 = min(video_size.height, y1 + area.height)
        w = (x2 - x1) & ~1  # round down to even (libx264 requirement)
        h = (y2 - y1) & ~1
        crop_size = (w, h)
        current_crop = (x1, y1, x1 + w, y1 + h)
        text_frame_count = 0

        current_output_path = output_dir / f"texts-{text_index:03d}.mp4"
        logger.info("Starting text clip {index} -> {path}", index=text_index, path=current_output_path)
        current_process = await create_subprocess_exec(
            "ffmpeg", "-y",
            "-f", "rawvideo", "-pix_fmt", "rgb24",
            "-framerate", str(video_size.fps),
            "-video_size", f"{w}x{h}",
            "-i", "pipe:0",
            "-c:v", "libx264", "-loglevel", "error",
            str(current_output_path),
            stdin=PIPE, stderr=PIPE,
        )
        text_index += 1

    def follow_center(area: Rectangle) -> None:
        nonlocal current_crop
        assert crop_size is not None
        w, h = crop_size
        x1 = max(0, area.cx - w // 2)
        y1 = max(0, area.cy - h // 2)
        x2 = min(video_size.width, x1 + w)
        y2 = min(video_size.height, y1 + h)
        current_crop = (x1, y1, x2, y2)

    async def process(frame: Frame, events: list[Event]) -> None:
        nonlocal previous_area, no_text_frames, text_frame_count

        text_events = [e for e in events if isinstance(e, TextEvent)]

        if text_events:
            no_text_frames = 0
            text_frame_count += 1
            x1 = min(e.area.cx - e.area.width  // 2 for e in text_events)
            y1 = min(e.area.cy - e.area.height // 2 for e in text_events)
            x2 = max(e.area.cx + e.area.width  // 2 for e in text_events)
            y2 = max(e.area.cy + e.area.height // 2 for e in text_events)
            area = Rectangle(cx=(x1 + x2) // 2, cy=(y1 + y2) // 2, width=x2 - x1, height=y2 - y1)

            is_close = (
                previous_area is not None
                and hypot(area.cx - previous_area.cx, area.cy - previous_area.cy) <= max_distance
            )

            if is_close:
                follow_center(area)
            else:
                await close_clip()
                await open_clip(area)

            previous_area = area  # set after close_clip so it isn't wiped
        else:
            no_text_frames += 1
            if no_text_frames > max_no_text_frames and current_process is not None:
                await close_clip()
                previous_area = None  # reset so next text starts a fresh clip
            elif previous_area is not None:
                follow_center(previous_area)  # hold crop at last known position

        if frame is not None and current_process is not None and current_crop is not None:
            assert crop_size is not None
            x1, y1, x2, y2 = current_crop
            cropped = frame[y1:y2, x1:x2]
            w, h = crop_size
            if cropped.shape[1] != w or cropped.shape[0] != h:
                cropped = np.array(Image.fromarray(cropped).resize((w, h), Image.Resampling.LANCZOS))
            assert current_process.stdin is not None
            current_process.stdin.write(cropped.tobytes())
            await current_process.stdin.drain()

    try:
        yield process
    finally:
        await close_clip()


@command()
@argument("video_file_path", type=Path)
def app(video_file_path: Path) -> None:
    async def coroutine() -> None:
        video_size = await probe_video(video_file_path)
        async with AsyncExitStack() as stack:
            detect_scene = await stack.enter_async_context(scene_detector())
            detect_text = await stack.enter_async_context(text_detector())
            write_scene = await stack.enter_async_context(scene_writer(video_size))
            write_text = await stack.enter_async_context(text_writer(video_size))
            frame_index = 0
            async for frame in read_video(video_file_path, video_size):
                logger.debug("Processing frame {frame_index}", frame_index=frame_index)
                events: list[Event] = []
                async for event in detect_scene(frame):
                    logger.info("Detected scene event: {event}", event=event)
                    events.append(event)
                async for event in detect_text(frame):
                    logger.info("Detected text event: {event}", event=event)
                    events.append(event)
                await write_scene(frame, events)
                await write_text(frame, events)
                frame_index += 1


    try:
        run(coroutine())
    except Exception:
        logger.exception("Fatal error")
        raise
