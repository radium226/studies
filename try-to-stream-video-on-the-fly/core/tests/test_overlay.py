"""overlay: pixel-level assertions for the dashed-rect / text helpers."""

import numpy as np

from video_analyzer.core.overlay import draw_caption_text, draw_dashed_rect


def test_draw_caption_text_paints_green_pixels() -> None:
    frame = np.zeros((100, 200, 3), dtype=np.uint8)
    draw_caption_text(frame, "hello")
    assert frame.any()
    # cv2.putText's green channel is what we asked for (0, 255, 0)
    ys, xs = np.nonzero(frame[:, :, 1])
    assert len(ys) > 0
    assert frame[ys[0], xs[0], 0] == 0
    assert frame[ys[0], xs[0], 2] == 0


def test_draw_dashed_rect_draws_something() -> None:
    frame = np.zeros((50, 50, 3), dtype=np.uint8)
    draw_dashed_rect(frame, (5, 5), (40, 40), (255, 255, 255))
    assert frame.any()


def test_draw_dashed_rect_zero_size_is_a_noop() -> None:
    frame = np.zeros((50, 50, 3), dtype=np.uint8)
    draw_dashed_rect(frame, (10, 10), (10, 10), (255, 255, 255))
    assert not frame.any()
