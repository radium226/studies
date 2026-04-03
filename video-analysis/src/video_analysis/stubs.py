from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass

from video_analysis.types import BoundingBox, Detect, Frame


@dataclass(frozen=True)
class TextInfo:
    text: str
    bbox: BoundingBox


@dataclass(frozen=True)
class FaceInfo:
    bbox: BoundingBox
    confidence: float


def stub_texts_detector(every: int = 10) -> Detect[TextInfo]:
    async def detect(
        frames: AsyncIterator[Frame],
    ) -> AsyncGenerator[tuple[TextInfo, float, float], None]:
        start_ts: float | None = None
        prev_ts = 0.0

        async for frame in frames:
            if frame.index % every == 0:
                if start_ts is None:
                    start_ts = frame.timestamp
                prev_ts = frame.timestamp
            elif start_ts is not None:
                yield (
                    TextInfo(
                        text=f"text@{start_ts:.2f}",
                        bbox=BoundingBox(0, 0, 100, 20),
                    ),
                    start_ts,
                    prev_ts,
                )
                start_ts = None

        if start_ts is not None:
            yield (
                TextInfo(
                    text=f"text@{start_ts:.2f}",
                    bbox=BoundingBox(0, 0, 100, 20),
                ),
                start_ts,
                prev_ts,
            )

    return detect


def stub_faces_detector(every: int = 15) -> Detect[FaceInfo]:
    async def detect(
        frames: AsyncIterator[Frame],
    ) -> AsyncGenerator[tuple[FaceInfo, float, float], None]:
        start_ts: float | None = None
        prev_ts = 0.0

        async for frame in frames:
            if frame.index % every == 0:
                if start_ts is None:
                    start_ts = frame.timestamp
                prev_ts = frame.timestamp
            elif start_ts is not None:
                yield (
                    FaceInfo(
                        bbox=BoundingBox(50, 50, 80, 80),
                        confidence=0.95,
                    ),
                    start_ts,
                    prev_ts,
                )
                start_ts = None

        if start_ts is not None:
            yield (
                FaceInfo(
                    bbox=BoundingBox(50, 50, 80, 80),
                    confidence=0.95,
                ),
                start_ts,
                prev_ts,
            )

    return detect
