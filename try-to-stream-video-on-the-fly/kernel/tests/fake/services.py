import asyncio
from typing import cast

from video_analyzer import kernel

from .models import Face, FaceEmbedding, Frame, FrameContent, TrackedFace


class Clock(kernel.Clock):
    """Advances by a fixed step on every call, so the token-bucket-backed
    `BatchGate` sees a moving clock instead of being permanently stuck at t=0."""

    def __init__(self, step: float = 0.1) -> None:
        self._time = 0.0
        self._step = step

    def now(self) -> float:
        current = self._time
        self._time += self._step
        return current


class SceneDetector(kernel.SceneDetector[FrameContent]):

    async def detect_scene_cut(
        self,
        previous_frame: Frame,
        current_frame: Frame,
    ) -> bool:
        return False


class FaceDetector(kernel.FaceDetector[FrameContent]):
    """Detects one face per frame, at a position that depends on the frame
    index, so interpolation across frames has something meaningful to fill."""

    async def detect_faces(
        self,
        frame_batch: list[Frame],
    ) -> list[list[kernel.Face[None]]]:
        return [
            [
                kernel.Face(
                    detection=kernel.Detection(
                        bounding_box=kernel.BoundingBox(
                            x=float(frame.index), y=0.0, width=10.0, height=10.0
                        ),
                        landmarks=(
                            (0.0, 0.0),
                            (1.0, 0.0),
                            (0.5, 0.5),
                            (0.0, 1.0),
                            (1.0, 1.0),
                        ),
                        confidence=1.0,
                    ),
                    embedding=None,
                )
            ]
            for frame in frame_batch
        ]


class FaceEmbedder(kernel.FaceEmbedder[FrameContent, FaceEmbedding]):

    async def embed_faces(
        self,
        face_batch: list[tuple[Frame, kernel.Face[None]]],
    ) -> list[Face]:
        return [face.with_embedding(0) for _, face in face_batch]


class Tracker(kernel.Tracker[FaceEmbedding]):
    """Assigns track ids by position — fine as long as fakes only ever
    produce a stable number of faces per frame."""

    async def update(self, faces: list[Face]) -> list[TrackedFace]:
        return [
            kernel.TrackedFace(track_id=index, face=face)
            for index, face in enumerate(faces)
        ]


class Interpolator(kernel.Interpolator[TrackedFace]):
    """Nearest-neighbor gap fill — no real spline math, just enough to
    exercise the pipeline's windowing/gap-fill wiring in tests."""

    async def interpolate(
        self,
        interpolables: list[TrackedFace | None],
    ) -> list[TrackedFace]:
        filled: list[TrackedFace | None] = list(interpolables)
        last: TrackedFace | None = None
        for index, value in enumerate(filled):
            if value is not None:
                last = value
            elif last is not None:
                filled[index] = last
        next_value: TrackedFace | None = None
        for index in range(len(filled) - 1, -1, -1):
            if filled[index] is not None:
                next_value = filled[index]
            elif next_value is not None:
                filled[index] = next_value
        assert all(value is not None for value in filled)
        return cast("list[TrackedFace]", filled)


class FrameSource(kernel.FrameSource[FrameContent]):

    def __init__(self, frames: list[Frame]) -> None:
        self.remaining_frames = list(frames)

    async def read_frame(self) -> Frame | None:
        # A real frame source always has a genuine suspension point here
        # (an ffmpeg pipe read); yielding here too lets the other pipeline
        # stages interleave instead of this task racing through every frame
        # in one uninterrupted scheduler turn.
        await asyncio.sleep(0)
        if not self.remaining_frames:
            return None
        return self.remaining_frames.pop(0)


class FrameSink(kernel.FrameSink[FrameContent, TrackedFace]):

    def __init__(self) -> None:
        self.written_frames: list[kernel.AnnotatedFrame[FrameContent, TrackedFace]] = []

    async def write_frame(
        self,
        annotated_frame: kernel.AnnotatedFrame[FrameContent, TrackedFace],
    ) -> None:
        self.written_frames.append(annotated_frame)


class FrameBroadcaster(kernel.FrameBroadcaster[FrameContent, TrackedFace]):

    def __init__(self) -> None:
        self.broadcast_frames: list[kernel.AnnotatedFrame[FrameContent, TrackedFace]] = []

    async def broadcast_frame(
        self,
        annotated_frame: kernel.AnnotatedFrame[FrameContent, TrackedFace],
    ) -> None:
        self.broadcast_frames.append(annotated_frame)
