from video_analyzer import kernel

from .models import Face, FaceEmbedding, Frame, FrameContent

type FrameBroadcaster = kernel.FrameBroadcaster[FrameContent]


class Clock(kernel.Clock):

    def now(self) -> float:
        return 0.0


class SceneDetector(kernel.SceneDetector[FrameContent]):

    def detect_scene_cut(
        self,
        previous_frame: Frame,
        current_frame: Frame,
    ) -> bool:
        return False


class FaceDetector(kernel.FaceDetector[FrameContent]):

    def detect_faces(
        self,
        frames: list[Frame],
    ) -> list[list[kernel.Face[None]]]:
        return [[] for _ in frames]


class FaceEmbedder(kernel.FaceEmbedder[FaceEmbedding]):

    def embed_faces(
        self,
        faces: list[kernel.Face[None]],
    ) -> list[Face]:
        return [face.with_embedding(0) for face in faces]


class FrameSource(kernel.FrameSource[FrameContent]):

    def __init__(self, frames: list[Frame]) -> None:
        self.remaining_frames = list(frames)

    def read_frame(self) -> Frame | None:
        if not self.remaining_frames:
            return None
        return self.remaining_frames.pop(0)


class FrameSink(kernel.FrameSink[FrameContent]):

    def __init__(self) -> None:
        self.written_frames: list[Frame] = []

    def write_frame(self, frame: Frame) -> None:
        self.written_frames.append(frame)
