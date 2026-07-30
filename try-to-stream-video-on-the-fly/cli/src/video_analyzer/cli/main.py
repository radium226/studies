"""Play a local video file or an http(s) URL (resolved via `yt-dlp`) with detected/tracked
faces drawn on it, via `ffplay`.

Wires `core`'s real SCRFD/ArcFace/ByteTrack/spline/histogram-scene-cut backends into
`kernel.Pipeline`, adding this package's own `FfplayFrameSink` plus a `NoopFrameBroadcaster`
stub for the one service slot neither `kernel` nor `core` implements.

All tuning lives in a YAML document (`--config`), not in flags — see `config.py` for the
schema and `--dump-config` for a filled-in copy of the defaults. A `scene_detector: null`
section swaps the histogram scene detector for a never-cuts `NoopSceneDetector`; a
`track_recording` section swaps the no-op broadcaster for `TrackRecordingFrameBroadcaster`,
which replays each discovered face track through its own `ffplay` window once the main video
finishes.
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

from .config import CliConfig, FfplayFrameSinkConfig
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
    frame_sink_config: FfplayFrameSinkConfig,
) -> None:
    """Play each recorded track's face crops back through its own `ffplay` window, one track
    at a time, in track-id order — after the main video's own window has already closed."""
    if not crops_by_track:
        logger.info("play-tracks: no tracks were found")
        return
    for track_id in sorted(crops_by_track):
        crops = crops_by_track[track_id]
        logger.info("play-tracks: playing track #{} ({} frames)", track_id, len(crops))
        async with FfplayFrameSink.start(
            crop_size, crop_size, fps, config=frame_sink_config
        ) as track_sink:
            for crop in crops:
                content = crop.copy()
                draw_caption_text(content, f"track #{track_id}")
                await track_sink.write_raw_frame(content)


def _is_url(source: str) -> bool:
    return source.startswith(("http://", "https://"))


async def _run(source: str, config: CliConfig) -> None:
    stop_token = kernel.StopToken()
    track_recorder: TrackRecordingFrameBroadcaster | None = None
    if _is_url(source):
        logger.info("resolving direct media URL via yt-dlp: {}", source)
        source = await core.resolve_direct_media_url(source)
    async with AsyncExitStack() as stack:
        raw_frame_source = await stack.enter_async_context(
            core.FfmpegFrameSource.start(source, config=config.frame_source)
        )
        # Always the file's native fps — `read_rate` paces how fast frames come
        # out, it doesn't change what the video *is*.
        video_info = raw_frame_source.video_info
        width, height, fps = video_info.width, video_info.height, video_info.fps

        frame_source: kernel.FrameSource = raw_frame_source
        if config.stop_after_frame_count is not None:
            frame_source = core.StopAfterFrameCount(
                frame_source, stop_token, config=config.stop_after_frame_count
            )

        face_detector = stack.enter_context(
            core.OnnxFaceDetector(config.models.scrfd, config=config.face_detector)
        )
        face_embedder = stack.enter_context(
            core.OnnxFaceEmbedder(config.models.arcface, config=config.face_embedder)
        )
        # `-framerate` on the sink is the other half of the time compression:
        # the decoder hands us frames N x faster (`frame_source.read_rate`), and
        # ffplay shows them N x faster, so playback stays balanced instead of
        # piling up behind a realtime-paced window.
        frame_sink = await stack.enter_async_context(
            FfplayFrameSink.start(
                width,
                height,
                fps * config.frame_source.read_rate,
                config=config.frame_sink,
            )
        )

        frame_broadcaster: kernel.FrameBroadcaster = NoopFrameBroadcaster()
        if config.track_recording is not None:
            track_recorder = TrackRecordingFrameBroadcaster(
                config=config.track_recording
            )
            frame_broadcaster = track_recorder
        if config.stop_on_first_track is not None:
            # The stop condition sits at the broadcast stage — the only place
            # interpolated frames exist — so "a track" really means a face on
            # screen for at least `min_track_frames` rendered frames, exact
            # and interpolated alike. A tracker-level condition could only
            # count sparse detection snapshots.
            frame_broadcaster = core.StopOnFirstTrack(
                frame_broadcaster, stop_token, config=config.stop_on_first_track
            )

        # Native fps here too: the tracker is stepped once per detection
        # snapshot, not per video frame, so its wall-clock update rate
        # doesn't move with playback speed.
        tracker: kernel.Tracker = core.ByteTrackTracker(fps, config=config.tracker)

        pipeline = kernel.Pipeline(
            clock=SystemClock(),
            scene_detector=(
                core.HistogramSceneDetector(config=config.scene_detector)
                if config.scene_detector is not None
                else NoopSceneDetector()
            ),
            face_detector=face_detector,
            face_embedder=face_embedder,
            tracker=tracker,
            interpolator=core.SplineInterpolator(config=config.interpolator),
            frame_sink=frame_sink,
            frame_broadcaster=frame_broadcaster,
            # Deliberately *not* scaled by `frame_source.read_rate`. This is
            # the detection budget (BatchGate's token bucket refill rate, in
            # tokens per wall-clock second), and holding it at native fps is
            # exactly what makes a faster playback cost detection coverage
            # rather than CPU: passes per wall-second stay put while N x more
            # frames flow past, so ~1/N of them get detected and the
            # interpolator fills the wider gaps.
            frames_per_second=fps,
            config=config.pipeline,
        )
        await pipeline.run(frame_source, stop_token=stop_token)

    if track_recorder is not None:
        await _play_tracks(
            track_recorder.crops_by_track,
            crop_size=track_recorder.config.crop_size,
            fps=video_info.fps * config.frame_source.read_rate,
            frame_sink_config=config.frame_sink,
        )


@click.command()
@click.argument("source", required=False)
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="YAML file holding every tuning knob (pipeline batching and lookahead, playback "
    "speed, models, detector/tracker/interpolator settings, early-stop and track-replay "
    "sections). Omit it to run on defaults. Start one from --dump-config.",
)
@click.option(
    "--dump-config",
    is_flag=True,
    default=False,
    help="Print the full default configuration as YAML and exit — every section and key, "
    "filled in with exactly what a bare run uses. This is the schema reference: redirect it "
    "to a file, edit, and pass it back with --config.",
)
def main(source: str | None, config_path: Path | None, dump_config: bool) -> None:
    _configure_logging()
    if dump_config:
        click.echo(CliConfig().to_yaml(), nl=False)
        return
    if source is None:
        raise click.UsageError("Missing argument 'SOURCE'.")
    # SOURCE is either a local video file or an http(s) page URL (resolved to a
    # direct media URL via yt-dlp). click.Path can't express that union, so the
    # file-existence check moves here.
    if not _is_url(source) and not Path(source).is_file():
        raise click.BadParameter(
            f"{source!r} is neither an existing video file nor an http(s) URL.",
            param_hint="SOURCE",
        )
    try:
        config = (
            CliConfig.from_yaml_file(config_path)
            if config_path is not None
            else CliConfig()
        )
    except kernel.ConfigError as error:
        # A typo in a config file is a user error, not a traceback.
        raise click.BadParameter(str(error), param_hint="--config") from error
    asyncio.run(_run(source, config))


if __name__ == "__main__":
    main()
