"""Play a local video file with detected/tracked faces drawn on it, via `ffplay`.

Wires `core`'s real SCRFD/ArcFace/ByteTrack/PCHIP backends into `kernel.Pipeline`, adding this
package's own `FfplayFrameSink` plus `NoopSceneDetector`/`NoopFrameBroadcaster` stubs for the two
service slots neither `kernel` nor `core` implements.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import click
from loguru import logger

from video_analyzer import core, kernel

from .ffplay_frame_sink import FfplayFrameSink
from .noop_frame_broadcaster import NoopFrameBroadcaster
from .noop_scene_detector import NoopSceneDetector
from .system_clock import SystemClock


def _configure_logging() -> None:
    """`kernel`/`core`/`cli` each disable their own logger by default (library etiquette — see
    kernel/CLAUDE.md); as the application entry point, opt back in and print everything,
    matching app/app.py's own `configure_logging()`."""
    logger.remove()
    logger.add(sys.stderr, level="TRACE")
    logger.enable("video_analyzer")


async def _run(
    video_path: Path,
    scrfd_model: Path,
    arcface_model: Path,
    *,
    speed_factor_target: float,
    batching: kernel.BatchingConfig,
    rendering: kernel.RenderingConfig,
) -> None:
    async with core.FfmpegFrameSource.start(
        str(video_path), loop=False, read_rate=speed_factor_target
    ) as frame_source:
        # Always the file's native fps — `read_rate` paces how fast frames come
        # out, it doesn't change what the video *is*.
        width, height, fps = frame_source.video_info
        face_detector = core.OnnxFaceDetector(scrfd_model)
        face_embedder = core.OnnxFaceEmbedder(arcface_model)
        with face_detector, face_embedder:
            # `-framerate` on the sink is the other half of the time compression:
            # the decoder hands us frames N x faster, and ffplay shows them N x
            # faster, so playback stays balanced instead of piling up behind a
            # realtime-paced window.
            async with FfplayFrameSink.start(
                width, height, fps * speed_factor_target
            ) as frame_sink:
                pipeline = kernel.Pipeline(
                    clock=SystemClock(),
                    scene_detector=NoopSceneDetector(),
                    face_detector=face_detector,
                    face_embedder=face_embedder,
                    # Native fps here too: the tracker is stepped once per
                    # detection snapshot, not per video frame, so its wall-clock
                    # update rate doesn't move with playback speed.
                    tracker=core.ByteTrackTracker(fps),
                    interpolator=core.PchipInterpolator(),
                    frame_sink=frame_sink,
                    frame_broadcaster=NoopFrameBroadcaster(),
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
                await pipeline.drain(frame_source)


@click.command()
@click.argument(
    "video_path", type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
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
def main(
    video_path: Path,
    scrfd_model: Path,
    arcface_model: Path,
    speed_factor_target: float,
    max_batch_frames: int,
    max_batch_lag_ms: float,
    lookahead: int,
) -> None:
    _configure_logging()
    asyncio.run(
        _run(
            video_path,
            scrfd_model,
            arcface_model,
            speed_factor_target=speed_factor_target,
            batching=kernel.BatchingConfig(
                max_frames=max_batch_frames, max_lag_ms=max_batch_lag_ms
            ),
            rendering=kernel.RenderingConfig(lookahead_snapshots=lookahead),
        )
    )


if __name__ == "__main__":
    main()
