"""ArcFace face embedding via ONNX Runtime, CPU provider."""

from __future__ import annotations

import asyncio
from concurrent.futures import Executor
from pathlib import Path
from typing import ClassVar, cast

import cv2
import numpy as np
from numpy.typing import NDArray

from video_analyzer import kernel

from ._onnx_model import OnnxModel
from .config import OnnxFaceEmbedderConfig

_ARCFACE_OUTPUT_SIZE: int = 112
_ARCFACE_MEAN: float = 127.5
_ARCFACE_STD: float = 128.0


class OnnxFaceEmbedder(OnnxModel, kernel.FaceEmbedder[NDArray[np.uint8], NDArray[np.float32]]):
    _REFERENCE_LANDMARKS: ClassVar[NDArray[np.float32]] = np.array(
        [
            [38.2946, 51.6963],
            [73.5318, 51.5014],
            [56.0252, 71.7366],
            [41.5493, 92.3655],
            [70.7299, 92.2041],
        ],
        dtype=np.float32,
    )

    def __init__(
        self,
        model_path: Path,
        executor: Executor | None = None,
        *,
        config: OnnxFaceEmbedderConfig | None = None,
    ) -> None:
        super().__init__(model_path)
        self.config = config if config is not None else OnnxFaceEmbedderConfig()
        self._executor = executor

    async def embed_faces(
        self,
        face_batch: list[tuple[kernel.Frame[NDArray[np.uint8]], kernel.Face[None]]],
    ) -> list[kernel.Face[NDArray[np.float32]]]:
        loop = asyncio.get_running_loop()
        items = [(frame.content, face) for frame, face in face_batch]
        return await loop.run_in_executor(self._executor, self._embed_batch, items)

    def _embed_batch(
        self,
        items: list[tuple[NDArray[np.uint8], kernel.Face[None]]],
    ) -> list[kernel.Face[NDArray[np.float32]]]:
        """Embed a batch of face crops, one embedding per (frame, face) item.

        Crops may come from different source frames; each is aligned against
        its own frame. ArcFace is run in chunks of at most `max_batch` crops so
        the inference batch stays bounded regardless of how many faces were
        found across the sampled frames.
        """
        session = self._require_session()
        if not items:
            return []

        prepared = [
            self._normalize_aligned_crop(self._align_face(frame, face.detection.landmarks))
            for frame, face in items
        ]
        input_name: str = session.get_inputs()[0].name
        chunk_size = self.config.max_batch

        embeddings: list[NDArray[np.float32]] = []
        for start in range(0, len(prepared), chunk_size):
            batch = np.stack(prepared[start : start + chunk_size]).astype(np.float32)
            raw = cast(
                "NDArray[np.float32]", session.run(None, {input_name: batch})[0]
            )
            for row in raw:
                norm = float(np.linalg.norm(row))
                embeddings.append(
                    (row / norm).astype(np.float32) if norm > 1e-6 else row
                )

        return [
            face.with_embedding(embedding)
            for (_, face), embedding in zip(items, embeddings, strict=True)
        ]

    def _align_face(
        self,
        img_bgr: NDArray[np.uint8],
        landmarks: kernel.FaceLandmarks,
    ) -> NDArray[np.uint8]:
        source_landmarks = np.array(
            [
                landmarks.left_eye,
                landmarks.right_eye,
                landmarks.nose,
                landmarks.mouth_left,
                landmarks.mouth_right,
            ],
            dtype=np.float32,
        )
        transform, _ = cv2.estimateAffinePartial2D(
            source_landmarks, self._REFERENCE_LANDMARKS, method=cv2.LMEDS
        )
        if transform is None:
            return cv2.resize(img_bgr, (_ARCFACE_OUTPUT_SIZE, _ARCFACE_OUTPUT_SIZE))
        return cv2.warpAffine(
            img_bgr, transform, (_ARCFACE_OUTPUT_SIZE, _ARCFACE_OUTPUT_SIZE)
        ).astype(np.uint8)

    def _normalize_aligned_crop(self, aligned_bgr: NDArray[np.uint8]) -> NDArray[np.float32]:
        img_rgb = aligned_bgr[:, :, ::-1].astype(np.float32)
        normalized = (img_rgb - _ARCFACE_MEAN) / _ARCFACE_STD
        return normalized.transpose(2, 0, 1)
