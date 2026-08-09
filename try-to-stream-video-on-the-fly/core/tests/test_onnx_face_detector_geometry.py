"""OnnxFaceDetector's session-free geometry: letterboxing, anchor grids,
distance decoding, and NMS. None of this needs model weights — the ONNX
session only exists between __enter__/__exit__, and these helpers run before
or after the actual inference call."""

from pathlib import Path

import numpy as np
import pytest

from video_analyzer.core.onnx_face_detector import (
    _SCRFD_INPUT_H,
    _SCRFD_INPUT_W,
    OnnxFaceDetector,
)


@pytest.fixture
def detector() -> OnnxFaceDetector:
    # The path is only dereferenced when the context manager loads a session.
    return OnnxFaceDetector(Path("unused.onnx"))


def test_letterbox_scales_and_pads_to_input_size(detector: OnnxFaceDetector) -> None:
    img = np.full((100, 200, 3), 255, dtype=np.uint8)

    tensor, scale = detector._letterbox(img)

    assert tensor.shape == (3, _SCRFD_INPUT_H, _SCRFD_INPUT_W)
    # Wider than tall: width is the binding axis.
    assert scale == pytest.approx(_SCRFD_INPUT_W / 200)
    scaled_h = int(100 * scale)
    # Content region is normalized white, padding is normalized black.
    assert tensor[:, :scaled_h, :].mean() == pytest.approx((255 - 127.5) / 128.0)
    assert tensor[:, scaled_h + 1 :, :].mean() == pytest.approx((0 - 127.5) / 128.0)


def test_letterbox_scale_maps_source_to_input_pixels(detector: OnnxFaceDetector) -> None:
    img = np.zeros((640, 640, 3), dtype=np.uint8)
    _, scale = detector._letterbox(img)
    assert scale == pytest.approx(1.0)


def test_anchors_shape_and_cache(detector: OnnxFaceDetector) -> None:
    stride = 32
    anchors = detector._anchors(stride)

    cells = (_SCRFD_INPUT_H // stride) * (_SCRFD_INPUT_W // stride)
    assert anchors.shape == (cells * 2, 2)  # 2 anchors per cell
    # Anchor centers are the cell origins in input pixels, duplicated per anchor.
    assert anchors[0].tolist() == [0.0, 0.0]
    assert anchors[1].tolist() == [0.0, 0.0]
    assert anchors[2].tolist() == [32.0, 0.0]
    assert detector._anchors(stride) is anchors  # built exactly once


def test_dist_to_bbox_decodes_left_top_right_bottom(detector: OnnxFaceDetector) -> None:
    centers = np.array([[10.0, 20.0]], dtype=np.float32)
    distances = np.array([[1.0, 2.0, 3.0, 4.0]], dtype=np.float32)

    bboxes = detector._dist_to_bbox(centers, distances)

    assert bboxes.tolist() == [[9.0, 18.0, 13.0, 24.0]]


def test_dist_to_landmarks_decodes_five_points(detector: OnnxFaceDetector) -> None:
    centers = np.array([[10.0, 20.0]], dtype=np.float32)
    distances = np.array([[1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]], dtype=np.float32)

    landmarks = detector._dist_to_landmarks(centers, distances)

    assert landmarks.shape == (1, 5, 2)
    assert landmarks[0].tolist() == [
        [11.0, 22.0],
        [13.0, 24.0],
        [15.0, 26.0],
        [17.0, 28.0],
        [19.0, 30.0],
    ]


def test_nms_suppresses_heavy_overlap_keeps_best_score(detector: OnnxFaceDetector) -> None:
    bboxes = np.array(
        [
            [0.0, 0.0, 10.0, 10.0],
            [1.0, 1.0, 11.0, 11.0],  # heavy overlap with the first
            [100.0, 100.0, 110.0, 110.0],  # far away
        ],
        dtype=np.float32,
    )
    scores = np.array([0.6, 0.9, 0.5], dtype=np.float32)

    kept = detector._nms(bboxes, scores)

    assert sorted(kept.tolist()) == [1, 2]


def test_nms_keeps_everything_without_overlap(detector: OnnxFaceDetector) -> None:
    bboxes = np.array(
        [[0.0, 0.0, 10.0, 10.0], [20.0, 20.0, 30.0, 30.0]], dtype=np.float32
    )
    scores = np.array([0.9, 0.8], dtype=np.float32)
    assert sorted(detector._nms(bboxes, scores).tolist()) == [0, 1]
