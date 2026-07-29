from dataclasses import dataclass

type FrameIndex = int


@dataclass(frozen=True, slots=True)
class Frame[FrameContentT]:
    index: FrameIndex
    content: FrameContentT
    # True when this frame opens a new scene (the SceneDetector reported a cut
    # between the previous frame and this one). Tracking and interpolation
    # never bridge a scene boundary.
    is_scene_start: bool = False
