from dataclasses import dataclass
from abc import ABC, abstractmethod

type FrameIndex = int


@dataclass(frozen=True, slots=True)
class Frame[PayloadT]:
    index: FrameIndex
    payload: PayloadT


class FrameSource[PayloadT](ABC):

    @abstractmethod
    def read_frame(self) -> Frame[PayloadT] | None:
        raise NotImplementedError()
    

class FrameSink[PayloadT](ABC):

    @abstractmethod
    def write_frame(self, frame: Frame[PayloadT]) -> None:
        raise NotImplementedError()

@dataclass(frozen=True, slots=True)
class BoundingBox:
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class Face[EmbeddingT]:
    bounding_box: BoundingBox
    embedding: EmbeddingT

    def with_embedding[NewEmbeddingT](self, embedding: NewEmbeddingT) -> "Face[NewEmbeddingT]":
        return Face(bounding_box=self.bounding_box, embedding=embedding)


class Clock(ABC):

    @abstractmethod
    def now(self) -> float:
        raise NotImplementedError()


class SceneDetector[FramePayloadT](ABC):

    @abstractmethod
    def detect_scene_cut(
        self, 
        previous_frame: Frame[FramePayloadT], 
        current_frame: Frame[FramePayloadT],
    ) -> bool:
        raise NotImplementedError()
    

class FaceDetector[FrameContentT](ABC):

    @abstractmethod
    def detect_faces(
        self, 
        frames: list[Frame[FrameContentT]],
    ) -> list[list[Face[None]]]:
        raise NotImplementedError()
    

class FrameBroadcaster[FrameContentT](ABC):

    @abstractmethod
    def broadcast_frame(self, frame: Frame[FrameContentT]) -> None:
        raise NotImplementedError()


class FaceEmbedder[FaceEmbeddingT](ABC):

    @abstractmethod
    def embed_faces(
        self, 
        faces: list[Face[None]],
    ) -> list[Face[FaceEmbeddingT]]:
        raise NotImplementedError()
    



class Pipeline[FrameContentT, FaceEmbeddingT]:

    clock: Clock
    scene_detector: SceneDetector[FrameContentT]
    face_detector: FaceDetector[FrameContentT]
    face_embedder: FaceEmbedder[FaceEmbeddingT]

    def __init__(
        self, 
        clock: Clock, 
        scene_detector: SceneDetector[FrameContentT], 
        face_detector: FaceDetector[FrameContentT], 
        face_embedder: FaceEmbedder[FaceEmbeddingT]
    ) -> None:
        self.clock = clock
        self.scene_detector = scene_detector
        self.face_detector = face_detector
        self.face_embedder = face_embedder


    def 

    def drain(self, frame_source: FrameSource[FrameContentT], frame_sink: FrameSink[FrameContentT]) -> None:
        