from video_analyzer import kernel

type FrameContent = int
type FaceEmbedding = int

Frame = kernel.Frame[FrameContent]
Face = kernel.Face[FaceEmbedding]
TrackedFace = kernel.TrackedFace[FaceEmbedding]
