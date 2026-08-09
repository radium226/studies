import asyncio

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
    """Cuts exactly at the scripted frame indices (never, by default)."""

    def __init__(self, cut_at: set[int] | None = None) -> None:
        self._cut_at = cut_at if cut_at is not None else set()

    async def detect_scene_cut(
        self,
        previous_frame: Frame,
        current_frame: Frame,
    ) -> bool:
        return current_frame.index in self._cut_at


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
                        landmarks=kernel.FaceLandmarks(
                            left_eye=(0.0, 0.0),
                            right_eye=(1.0, 0.0),
                            nose=(0.5, 0.5),
                            mouth_left=(0.0, 1.0),
                            mouth_right=(1.0, 1.0),
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
    produce a stable number of faces per frame. Each `reset` bumps a
    generation prefix, so a test can tell which scene an id was assigned in
    and ids never collide across a reset."""

    def __init__(self) -> None:
        self.reset_count = 0

    async def update(self, faces: list[Face]) -> list[TrackedFace]:
        return [
            kernel.TrackedFace(
                track_id=f"{self.reset_count}:{index}", face=face
            )
            for index, face in enumerate(faces)
        ]

    async def reset(self) -> None:
        self.reset_count += 1


class Interpolator(kernel.Interpolator[TrackedFace]):
    """Nearest-neighbor point query — no real spline math, just enough to
    exercise the pipeline's windowing/gap-fill wiring in tests."""

    async def interpolate(
        self,
        interpolables: list[TrackedFace | None],
        at: int,
    ) -> TrackedFace:
        if (known := interpolables[at]) is not None:
            return known
        best: TrackedFace | None = None
        best_distance: int | None = None
        for index, value in enumerate(interpolables):
            if value is None:
                continue
            distance = abs(index - at)
            if best_distance is None or distance < best_distance:
                best, best_distance = value, distance
        assert best is not None, "caller guarantees at least 2 known points"
        return best


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
