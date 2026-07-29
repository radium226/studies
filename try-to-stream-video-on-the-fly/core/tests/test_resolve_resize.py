"""resolve_resize: the -1 aspect-ratio placeholders and the single
round-down-to-even rule."""

from video_analyzer.core import resolve_resize


def test_native_passthrough_keeps_even_dimensions() -> None:
    assert resolve_resize(1280, 720, (-1, -1)) == (1280, 720)


def test_native_passthrough_rounds_odd_dimensions_down_to_even() -> None:
    assert resolve_resize(1281, 721, (-1, -1)) == (1280, 720)


def test_explicit_dimensions_round_down_to_even() -> None:
    assert resolve_resize(1280, 720, (641, 361)) == (640, 360)


def test_width_placeholder_preserves_aspect_ratio() -> None:
    # 1280/720 * 360 = 640
    assert resolve_resize(1280, 720, (-1, 360)) == (640, 360)


def test_height_placeholder_preserves_aspect_ratio() -> None:
    assert resolve_resize(1280, 720, (640, -1)) == (640, 360)


def test_computed_axis_rounds_down_to_even() -> None:
    # 1280/720 * 350 = 622.2... -> 622 (already even)
    assert resolve_resize(1280, 720, (-1, 350)) == (622, 350)
    # 999*250/500 = 499.5 -> round() -> 500 (already even)
    assert resolve_resize(999, 500, (-1, 250)) == (500, 250)
    # 998*250/500 = 499 -> odd, rounds down to even
    assert resolve_resize(998, 500, (-1, 250)) == (498, 250)


def test_tiny_targets_clamp_to_minimum_even_size() -> None:
    assert resolve_resize(1280, 720, (1, 1)) == (2, 2)
