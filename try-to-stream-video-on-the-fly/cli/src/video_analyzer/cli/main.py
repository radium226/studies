"""Play a local video file or an http(s) URL (resolved via `yt-dlp`) with detected/tracked
faces drawn on it, via `ffplay`.

Wires `core`'s real SCRFD/ArcFace/ByteTrack/spline/histogram-scene-cut backends into
`kernel.Pipeline`, adding this package's own `FfplayFrameSink` plus a `NoopFrameBroadcaster`
stub for the one service slot neither `kernel` nor `core` implements. `--no-scene-detection`
swaps the histogram scene detector for a never-cuts `NoopSceneDetector`; `--play-tracks` swaps
the no-op broadcaster for `TrackRecordingFrameBroadcaster`, which replays each discovered face
track through its own `ffplay` window once the main video finishes.
"""

from __future__ import annotations

import asyncio
import sys
from contextlib import AsyncExitStack
from pathlib import Path

import click
import numpy as np
from loguru import logger
from numpy.typing import NDArray
from video_analyzer.core.overlay import draw_caption_text

from video_analyzer import core, kernel

from .ffplay_frame_sink import FfplayFrameSink
from .noop_frame_broadcaster import NoopFrameBroadcaster
from .noop_scene_detector import NoopSceneDetector
from .system_clock import SystemClock
from .track_recording_frame_broadcaster import TrackRecordingFrameBroadcaster


def _configure_logging() -> None:
    """`kernel`/`core`/`cli` each disable their own logger by default (library etiquette — see
    kernel/CLAUDE.md); as the application entry point, opt back in and print everything,
    matching app/app.py's own `configure_logging()`."""
    logger.remove()
    logger.add(sys.stderr, level="TRACE")
    logger.enable("video_analyzer")


async def _play_tracks(
    crops_by_track: dict[int, list[NDArray[np.uint8]]],
    *,
    crop_size: int,
    fps: float,
) -> None:
    """Play each recorded track's face crops back through its own `ffplay` window, one track
    at a time, in track-id order — after the main video's own window has already closed."""
    if not crops_by_track:
        logger.info("play-tracks: no tracks were found")
        return
    for track_id in sorted(crops_by_track):
        crops = crops_by_track[track_id]
        logger.info("play-tracks: playing track #{} ({} frames)", track_id, len(crops))
        async with FfplayFrameSink.start(crop_size, crop_size, fps) as track_sink:
            for crop in crops:
                content = crop.copy()
                draw_caption_text(content, f"track #{track_id}")
                await track_sink.write_raw_frame(content)


def _is_url(source: str) -> bool:
    return source.startswith(("http://", "https://"))


async def _run(
    source: str,
    scrfd_model: Path,
    arcface_model: Path,
    *,
    speed_factor_target: float,
    batching: kernel.BatchingConfig,
    rendering: kernel.RenderingConfig,
    scene_detection: bool,
    stop_after_frames: int | None,
    stop_on_first_track: bool,
    play_tracks: bool,
    track_crop_size: int,
    max_track_crops: int | None,
) -> None:
    stop_token = kernel.StopToken()
    track_recorder: TrackRecordingFrameBroadcaster | None = None
    if _is_url(source):
        logger.info("resolving direct media URL via yt-dlp: {}", source)
        source = await core.resolve_direct_media_url(source)
    async with AsyncExitStack() as stack:
        raw_frame_source = await stack.enter_async_context(
            core.FfmpegFrameSource.start(
                source, loop=False, read_rate=speed_factor_target
            )
        )
        # Always the file's native fps — `read_rate` paces how fast frames come
        # out, it doesn't change what the video *is*.
        video_info = raw_frame_source.video_info
        width, height, fps = video_info.width, video_info.height, video_info.fps

        frame_source: kernel.FrameSource = raw_frame_source
        if stop_after_frames is not None:
            frame_source = core.StopAfterFrameCount(
                frame_source, stop_token, stop_after_frames
            )

        face_detector = stack.enter_context(core.OnnxFaceDetector(scrfd_model))
        face_embedder = stack.enter_context(core.OnnxFaceEmbedder(arcface_model))
        # `-framerate` on the sink is the other half of the time compression:
        # the decoder hands us frames N x faster, and ffplay shows them N x
        # faster, so playback stays balanced instead of piling up behind a
        # realtime-paced window.
        frame_sink = await stack.enter_async_context(
            FfplayFrameSink.start(width, height, fps * speed_factor_target)
        )

        frame_broadcaster: kernel.FrameBroadcaster = NoopFrameBroadcaster()
        if play_tracks:
            track_recorder = TrackRecordingFrameBroadcaster(
                crop_size=track_crop_size, max_crops_per_track=max_track_crops
            )
            frame_broadcaster = track_recorder

        # Native fps here too: the tracker is stepped once per detection
        # snapshot, not per video frame, so its wall-clock update rate
        # doesn't move with playback speed.
        tracker: kernel.Tracker = core.ByteTrackTracker(fps)
        if stop_on_first_track:
            tracker = core.StopOnFirstTrack(tracker, stop_token)

        pipeline = kernel.Pipeline(
            clock=SystemClock(),
            scene_detector=(
                core.HistogramSceneDetector() if scene_detection else NoopSceneDetector()
            ),
            face_detector=face_detector,
            face_embedder=face_embedder,
            tracker=tracker,
            interpolator=core.SplineInterpolator(),
            frame_sink=frame_sink,
            frame_broadcaster=frame_broadcaster,
            config=kernel.PipelineConfig(
                # Deliberately *not* scaled by speed_factor_target. This
                # is the detection budget (BatchGate's token bucket
                # refill rate, in tokens per wall-clock second), and
                # holding it at native fps is exactly what makes a
                # faster playback cost detection coverage rather than
                # CPU: passes per wall-second stay put while N x more
                # frames flow past, so ~1/N of them get detected and
                # the interpolator fills the wider gaps.
                frames_per_second=fps,
                batching=batching,
                rendering=rendering,
            ),
        )
        await pipeline.run(frame_source, stop_token=stop_token)

    if track_recorder is not None:
        await _play_tracks(
            track_recorder.crops_by_track,
            crop_size=track_crop_size,
            fps=video_info.fps * speed_factor_target,
        )


@click.command()
@click.argument("source")
@click.option(
    "--scrfd-model",
    default="../app/models/scrfd_10g_kps_dynamic.onnx",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    show_default=True,
    help="Path to the SCRFD ONNX weights.",
)
@click.option(
    "--arcface-model",
    default="../app/models/arcface_w600k_r50_batch.onnx",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    show_default=True,
    help="Path to the ArcFace ONNX weights.",
)
@click.option(
    "--speed-factor-target",
    type=click.FloatRange(min=0.0, min_open=True),
    default=1.0,
    show_default=True,
    help="Playback speed multiplier to aim for: scales both the decoder read-rate and "
    "ffplay's frame rate (e.g. 4 = four times as fast, 0.5 = slow motion). Every frame "
    "is still decoded and drawn — the speed-up is time-compression, paid for with "
    "detection coverage (roughly 1/N of the frames get detected; interpolation fills the "
    "rest). A 'target' because the achieved speed caps at whatever the ffplay sink can "
    "actually sustain.",
)
@click.option(
    "--max-batch-frames",
    type=click.IntRange(min=1),
    default=4,
    show_default=True,
    help="Number of frames per batched SCRFD detection pass. Raise it alongside "
    "--speed-factor-target to buy detection coverage back, at more work per pass.",
)
@click.option(
    "--max-batch-lag-ms",
    type=click.FloatRange(min=0.0),
    default=0.0,
    show_default=True,
    help="Max ms to wait for a full detection batch before firing with fewer frames "
    "(0 = fire immediately).",
)
@click.option(
    "--lookahead",
    type=click.IntRange(min=0),
    default=3,
    show_default=True,
    help="Interpolation lookahead in detection snapshots. Higher = smoother splines but "
    "more lag.",
)
@click.option(
    "--scene-detection/--no-scene-detection",
    default=True,
    show_default=True,
    help="Detect hard cuts (histogram correlation of consecutive frames) and reset face "
    "tracking at each cut, so identities and interpolation never bridge scenes. Disable "
    "to track straight through cuts.",
)
@click.option(
    "--stop-after-frames",
    type=click.IntRange(min=1),
    default=None,
    help="Stop the pipeline early after this many frames have been read. Frames already "
    "read still drain all the way through detection/tracking/interpolation/render — this "
    "doesn't cut playback off mid-frame, it just stops feeding the pipeline further input.",
)
@click.option(
    "--stop-on-first-track",
    is_flag=True,
    default=False,
    help="Stop the pipeline early as soon as the tracker confirms its first face track. "
    "Graceful, like --stop-after-frames: frames already read still drain all the way "
    "through the pipeline, so playback continues briefly past the trigger.",
)
@click.option(
    "--play-tracks",
    is_flag=True,
    default=False,
    help="After the main video finishes, replay each discovered face track's crop through "
    "its own ffplay window, one track at a time, in track-id order. Every rendered frame's "
    "faces are buffered in memory for the whole run, so this costs more RAM the longer the "
    "video and the more faces it contains.",
)
@click.option(
    "--track-crop-size",
    type=click.IntRange(min=16),
    default=160,
    show_default=True,
    help="Side length (pixels) each face crop is resized to for --play-tracks playback.",
)
@click.option(
    "--max-track-crops",
    type=click.IntRange(min=0),
    default=300,
    show_default=True,
    help="Bound on buffered crops per track for --play-tracks: each track's replay keeps "
    "only its first N rendered frames (~10 s at 30 fps by default), capping memory at "
    "about N x crop_size^2 x 3 bytes per track (~22 MB at the defaults). 0 = unbounded, "
    "record every frame of every track for the whole run.",
)
def main(
    source: str,
    scrfd_model: Path,
    arcface_model: Path,
    speed_factor_target: float,
    max_batch_frames: int,
    max_batch_lag_ms: float,
    lookahead: int,
    scene_detection: bool,
    stop_after_frames: int | None,
    stop_on_first_track: bool,
    play_tracks: bool,
    track_crop_size: int,
    max_track_crops: int,
) -> None:
    _configure_logging()
    # SOURCE is either a local video file or an http(s) page URL (resolved to a
    # direct media URL via yt-dlp). click.Path can't express that union, so the
    # file-existence check moves here.
    if not _is_url(source) and not Path(source).is_file():
        raise click.BadParameter(
            f"{source!r} is neither an existing video file nor an http(s) URL.",
            param_hint="SOURCE",
        )
    asyncio.run(
        _run(
            source,
            scrfd_model,
            arcface_model,
            speed_factor_target=speed_factor_target,
            batching=kernel.BatchingConfig(
                max_frames=max_batch_frames, max_lag_ms=max_batch_lag_ms
            ),
            rendering=kernel.RenderingConfig(lookahead_snapshots=lookahead),
            scene_detection=scene_detection,
            stop_after_frames=stop_after_frames,
            stop_on_first_track=stop_on_first_track,
            play_tracks=play_tracks,
            track_crop_size=track_crop_size,
            # click can't express "int or unlimited" in one type, so 0 is the
            # CLI spelling of the recorder's None.
            max_track_crops=max_track_crops if max_track_crops > 0 else None,
        )
    )


if __name__ == "__main__":
    main()
