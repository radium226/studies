"""Thin wrapper around the OpenCV calls used to prove frames really flow through numpy."""

from __future__ import annotations

import cv2
import numpy as np


def draw_overlay(frame: np.ndarray, text: str) -> np.ndarray:
    return cv2.putText(
        frame.copy(),
        text,
        (10, 30),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (0, 255, 0),
        2,
        cv2.LINE_AA,
    )
