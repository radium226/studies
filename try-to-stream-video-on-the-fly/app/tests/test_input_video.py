"""_resolve_resize: even-dimension clamping and -1 aspect-ratio placeholders."""

from video_streamer.input_video import _resolve_resize


def test_native_even_dims_are_unchanged() -> None:
    assert _resolve_resize(1280, 720, (-1, -1)) == (1280, 720)


def test_native_odd_dims_round_down_to_even() -> None:
    # yuv420p/libx264 requires even width and height; an odd native source
    # (common with arbitrary web video URLs) must never pass through as-is.
    assert _resolve_resize(639, 359, (-1, -1)) == (638, 358)


def test_explicit_odd_dims_round_down_to_even() -> None:
    assert _resolve_resize(1280, 720, (641, 361)) == (640, 360)


def test_aspect_preserving_width_rounds_to_even() -> None:
    # 1280x720 -> width 640, height computed from aspect ratio (360, already even).
    assert _resolve_resize(1280, 720, (640, -1)) == (640, 360)


def test_aspect_preserving_width_computed_odd_rounds_to_even() -> None:
    # src aspect ratio produces an odd computed width (round(639*100/480) ==
    # 133), which must still come out even after the aspect-branch rounding.
    assert _resolve_resize(639, 480, (-1, 100)) == (134, 100)
