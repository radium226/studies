"""cv2 drawing helpers for rendering detections onto a frame before it reaches
a `FrameSink`. Pure functions — no ABC to satisfy, just useful building blocks
for whoever assembles a `core`-backed pipeline and wants boxes burned in."""

from __future__ import annotations

import cv2
import numpy as np


def draw_overlay(frame: np.ndarray, text: str) -> None:
    """Draw a text line in-place. The caller owns the copy — draw onto a
    delayed/annotated frame, never the pristine one still awaiting detection."""
    cv2.putText(
        frame,
        text,
        (10, 30),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (0, 255, 0),
        2,
        cv2.LINE_AA,
    )


def draw_dashed_rect(
    img: np.ndarray,
    pt1: tuple[int, int],
    pt2: tuple[int, int],
    color: tuple[int, int, int],
    thickness: int = 2,
    dash_len: int = 10,
) -> None:
    """Draw a dashed rectangle in-place — conventionally used for interpolated
    (non-exact) detections, a solid rectangle for ones that landed exactly on
    a real detection."""
    x1, y1 = pt1
    x2, y2 = pt2
    edges: list[tuple[tuple[int, int], tuple[int, int]]] = [
        ((x1, y1), (x2, y1)),  # top
        ((x2, y1), (x2, y2)),  # right
        ((x2, y2), (x1, y2)),  # bottom
        ((x1, y2), (x1, y1)),  # left
    ]
    for (ax, ay), (bx, by) in edges:
        length = int(((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5)
        if length == 0:
            continue
        dx, dy = (bx - ax) / length, (by - ay) / length
        pos = 0
        draw = True
        while pos < length:
            end = min(pos + dash_len, length)
            if draw:
                sx, sy = int(ax + dx * pos), int(ay + dy * pos)
                ex, ey = int(ax + dx * end), int(ay + dy * end)
                cv2.line(img, (sx, sy), (ex, ey), color, thickness)
            pos = end
            draw = not draw
