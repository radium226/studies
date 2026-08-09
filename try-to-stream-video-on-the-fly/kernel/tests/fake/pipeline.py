from video_analyzer import kernel

from .models import FaceEmbedding, FrameContent

Pipeline = kernel.Pipeline[FrameContent, FaceEmbedding]
