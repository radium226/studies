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

# Matches app/'s own --lookahead default: smooth enough for a demo without extra flags.
_LOOKAHEAD_SNAPSHOTS = 3


def _configure_logging() -> None:
    """`kernel`/`core`/`cli` each disable their own logger by default (library etiquette — see
    kernel/CLAUDE.md); as the application entry point, opt back in and print everything,
    matching app/app.py's own `configure_logging()`."""
    logger.remove()
    logger.add(sys.stderr, level="TRACE")
    logger.enable("video_analyzer")


async def _run(video_path: Path, scrfd_model: Path, arcface_model: Path) -> None:
    async with core.FfmpegFrameSource.start(str(video_path), loop=False) as frame_source:
        width, height, fps = frame_source.video_info
        face_detector = core.OnnxFaceDetector(scrfd_model)
        face_embedder = core.OnnxFaceEmbedder(arcface_model)
        with face_detector, face_embedder:
            async with FfplayFrameSink.start(width, height, fps) as frame_sink:
                pipeline = kernel.Pipeline(
                    clock=SystemClock(),
                    scene_detector=NoopSceneDetector(),
                    face_detector=face_detector,
                    face_embedder=face_embedder,
                    tracker=core.ByteTrackTracker(fps),
                    interpolator=core.PchipInterpolator(),
                    frame_sink=frame_sink,
                    frame_broadcaster=NoopFrameBroadcaster(),
                    config=kernel.PipelineConfig(
                        frames_per_second=fps,
                        rendering=kernel.RenderingConfig(
                            lookahead_snapshots=_LOOKAHEAD_SNAPSHOTS
                        ),
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
def main(video_path: Path, scrfd_model: Path, arcface_model: Path) -> None:
    _configure_logging()
    asyncio.run(_run(video_path, scrfd_model, arcface_model))


if __name__ == "__main__":
    main()
