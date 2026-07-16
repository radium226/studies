from dataclasses import dataclass

type FrameIndex = int


@dataclass(frozen=True, slots=True)
class Frame[FrameContentT]:
    index: FrameIndex
    content: FrameContentT
