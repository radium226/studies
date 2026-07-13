"""Face detection (SCRFD) and embedding (ArcFace) via ONNX."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, ClassVar, cast

import cv2
import numpy as np
from numpy.typing import NDArray

if TYPE_CHECKING:
    import onnxruntime as ort

_SCRFD_INPUT_SIZE: tuple[int, int] = (640, 640)
_SCRFD_STRIDES: list[int] = [8, 16, 32]
_SCRFD_NUM_ANCHORS: int = 2
_SCRFD_MEAN: float = 127.5
_SCRFD_STD: float = 128.0

_ARCFACE_OUTPUT_SIZE: int = 112
_ARCFACE_MEAN: float = 127.5
_ARCFACE_STD: float = 128.0


@dataclass(frozen=True, slots=True)
class Detection:
    bbox: tuple[float, float, float, float]  # x1, y1, x2, y2
    landmarks: NDArray[np.float32]  # shape (5, 2)
    confidence: float


class FaceDetector:
    def __init__(
        self,
        model_path: Path,
        score_threshold: float = 0.5,
        iou_threshold: float = 0.4,
    ) -> None:
        self.model_path = model_path
        self.score_threshold = score_threshold
        self.iou_threshold = iou_threshold
        self._session: ort.InferenceSession | None = None

    def __enter__(self) -> FaceDetector:
        import onnxruntime as ort

        self._session = ort.InferenceSession(
            str(self.model_path), providers=["CPUExecutionProvider"]
        )
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self._session = None

    def detect(self, frame: NDArray[np.uint8]) -> list[Detection]:
        return self.detect_batch([frame])[0]

    def detect_batch(
        self, frames: list[NDArray[np.uint8]]
    ) -> list[list[Detection]]:
        if self._session is None:
            raise RuntimeError("FaceDetector not open — use as a context manager")
        if not frames:
            return []

        input_h, input_w = _SCRFD_INPUT_SIZE
        # Each frame is letterboxed to 640x640, so preprocessed tensors share a
        # shape and stack cleanly even if the source frames differ in size. The
        # per-frame scale (used to rescale detections back to source pixels) is
        # kept alongside so it can be applied when decoding that batch element.
        scales = [
            min(input_h / f.shape[0], input_w / f.shape[1]) for f in frames
        ]
        batch = np.stack([self._preprocess(f) for f in frames]).astype(np.float32)
        input_name: str = self._session.get_inputs()[0].name
        outputs = cast(
            "list[NDArray[np.float32]]", self._session.run(None, {input_name: batch})
        )

        results: list[list[Detection]] = []
        for b, scale in enumerate(scales):
            image_outputs = [o[b] for o in outputs]
            scores, bboxes, landmarks = self._decode(image_outputs, input_h, input_w)

            if len(bboxes) == 0:
                results.append([])
                continue

            kept = self._nms(bboxes, scores)
            scores = scores[kept]
            bboxes = bboxes[kept]
            landmarks = landmarks[kept]

            detections: list[Detection] = []
            for bbox, lm, score in zip(bboxes, landmarks, scores):
                detections.append(
                    Detection(
                        bbox=(
                            float(bbox[0]) / scale,
                            float(bbox[1]) / scale,
                            float(bbox[2]) / scale,
                            float(bbox[3]) / scale,
                        ),
                        landmarks=(lm / scale).astype(np.float32),
                        confidence=float(score),
                    )
                )
            results.append(detections)
        return results

    def _preprocess(self, img_bgr: NDArray[np.uint8]) -> NDArray[np.float32]:
        input_w, input_h = _SCRFD_INPUT_SIZE
        img_h, img_w = img_bgr.shape[:2]
        scale = min(input_h / img_h, input_w / img_w)
        rw, rh = int(img_w * scale), int(img_h * scale)
        resized = cv2.resize(img_bgr, (rw, rh))
        padded = np.zeros((input_h, input_w, 3), dtype=np.uint8)
        padded[:rh, :rw] = resized
        img_rgb = padded[:, :, ::-1].astype(np.float32)
        normalized = (img_rgb - _SCRFD_MEAN) / _SCRFD_STD
        return normalized.transpose(2, 0, 1)

    def _generate_anchors(
        self, input_h: int, input_w: int, stride: int
    ) -> NDArray[np.float32]:
        fh, fw = input_h // stride, input_w // stride
        grid_y, grid_x = np.mgrid[:fh, :fw]
        centers = np.stack([grid_x, grid_y], axis=-1).astype(np.float32)
        centers = (centers * stride).reshape(-1, 2)
        return np.stack([centers] * _SCRFD_NUM_ANCHORS, axis=1).reshape(-1, 2)

    def _dist_to_bbox(
        self,
        centers: NDArray[np.float32],
        distances: NDArray[np.float32],
    ) -> NDArray[np.float32]:
        x1 = centers[:, 0] - distances[:, 0]
        y1 = centers[:, 1] - distances[:, 1]
        x2 = centers[:, 0] + distances[:, 2]
        y2 = centers[:, 1] + distances[:, 3]
        return np.stack([x1, y1, x2, y2], axis=-1)

    def _dist_to_landmarks(
        self,
        centers: NDArray[np.float32],
        distances: NDArray[np.float32],
    ) -> NDArray[np.float32]:
        coords: list[NDArray[np.float32]] = []
        for i in range(0, distances.shape[1], 2):
            coords.append(centers[:, 0] + distances[:, i])
            coords.append(centers[:, 1] + distances[:, i + 1])
        return np.stack(coords, axis=-1).reshape(-1, 5, 2)

    def _nms(
        self,
        bboxes: NDArray[np.float32],
        scores: NDArray[np.float32],
    ) -> NDArray[np.int64]:
        x1, y1, x2, y2 = bboxes[:, 0], bboxes[:, 1], bboxes[:, 2], bboxes[:, 3]
        areas = (x2 - x1) * (y2 - y1)
        order = scores.argsort()[::-1]
        kept: list[int] = []
        while len(order) > 0:
            top = int(order[0])
            kept.append(top)
            rest = order[1:]
            ix1 = np.maximum(x1[top], x1[rest])
            iy1 = np.maximum(y1[top], y1[rest])
            ix2 = np.minimum(x2[top], x2[rest])
            iy2 = np.minimum(y2[top], y2[rest])
            inter = np.maximum(0.0, ix2 - ix1) * np.maximum(0.0, iy2 - iy1)
            iou = inter / (areas[top] + areas[rest] - inter)
            order = rest[iou <= self.iou_threshold]
        return np.array(kept, dtype=np.int64)

    def _decode(
        self,
        outputs: list[NDArray[np.float32]],
        input_h: int,
        input_w: int,
    ) -> tuple[NDArray[np.float32], NDArray[np.float32], NDArray[np.float32]]:
        all_scores: list[NDArray[np.float32]] = []
        all_bboxes: list[NDArray[np.float32]] = []
        all_landmarks: list[NDArray[np.float32]] = []

        for i, stride in enumerate(_SCRFD_STRIDES):
            scores: NDArray[np.float32] = outputs[i].flatten()
            bbox_dists: NDArray[np.float32] = outputs[3 + i] * stride
            lm_dists: NDArray[np.float32] = outputs[6 + i] * stride

            centers = self._generate_anchors(input_h, input_w, stride)
            bboxes = self._dist_to_bbox(centers, bbox_dists)
            landmarks = self._dist_to_landmarks(centers, lm_dists)

            mask = scores >= self.score_threshold
            all_scores.append(scores[mask])
            all_bboxes.append(bboxes[mask])
            all_landmarks.append(landmarks[mask])

        return (
            np.concatenate(all_scores, axis=0),
            np.concatenate(all_bboxes, axis=0),
            np.concatenate(all_landmarks, axis=0),
        )


class FaceEmbedder:
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

    def __init__(self, model_path: Path) -> None:
        self.model_path = model_path
        self._session: ort.InferenceSession | None = None

    def __enter__(self) -> FaceEmbedder:
        import onnxruntime as ort

        self._session = ort.InferenceSession(
            str(self.model_path), providers=["CPUExecutionProvider"]
        )
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self._session = None

    def embed(
        self, frame: NDArray[np.uint8], detections: list[Detection]
    ) -> list[NDArray[np.float32]]:
        return self.embed_many([(frame, det) for det in detections], max_batch=len(detections) or 1)

    def embed_many(
        self,
        items: list[tuple[NDArray[np.uint8], Detection]],
        max_batch: int,
    ) -> list[NDArray[np.float32]]:
        """Embed a batch of face crops, one embedding per (frame, detection) item.

        Crops may come from different source frames; each is aligned against its
        own frame. ArcFace is run in chunks of at most `max_batch` crops so the
        inference batch stays bounded regardless of how many faces were found.
        """
        if self._session is None:
            raise RuntimeError("FaceEmbedder not open — use as a context manager")
        if not items:
            return []

        prepared = [
            self._preprocess(self._align(frame, det.landmarks)) for frame, det in items
        ]
        input_name: str = self._session.get_inputs()[0].name
        chunk = max(1, max_batch)

        results: list[NDArray[np.float32]] = []
        for start in range(0, len(prepared), chunk):
            batch = np.stack(prepared[start : start + chunk]).astype(np.float32)
            raw = cast(
                "NDArray[np.float32]", self._session.run(None, {input_name: batch})[0]
            )
            for row in raw:
                norm = float(np.linalg.norm(row))
                results.append((row / norm).astype(np.float32) if norm > 1e-6 else row)
        return results

    def _align(
        self, img_bgr: NDArray[np.uint8], landmarks: NDArray[np.float32]
    ) -> NDArray[np.uint8]:
        transform, _ = cv2.estimateAffinePartial2D(
            landmarks, self._REFERENCE_LANDMARKS, method=cv2.LMEDS
        )
        if transform is None:
            return cv2.resize(img_bgr, (_ARCFACE_OUTPUT_SIZE, _ARCFACE_OUTPUT_SIZE))
        return cv2.warpAffine(
            img_bgr, transform, (_ARCFACE_OUTPUT_SIZE, _ARCFACE_OUTPUT_SIZE)
        ).astype(np.uint8)

    def _preprocess(self, aligned_bgr: NDArray[np.uint8]) -> NDArray[np.float32]:
        img_rgb = aligned_bgr[:, :, ::-1].astype(np.float32)
        normalized = (img_rgb - _ARCFACE_MEAN) / _ARCFACE_STD
        return normalized.transpose(2, 0, 1)
