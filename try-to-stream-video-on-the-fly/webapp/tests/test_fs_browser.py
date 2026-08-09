"""fs_browser: listing an arbitrary directory for the browse modal, and resolving a picked
absolute path back to an existing video file. Unlike the old VideoLibrary, there's no directory
allowlist here — these tests exercise real, arbitrary tmp_path locations."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from video_analyzer.webapp import fs_browser


def _touch(path: Path, content: bytes = b"") -> Path:
    path.write_bytes(content)
    return path


def test_list_directory_filters_by_extension_case_insensitively(tmp_path: Path) -> None:
    _touch(tmp_path / "a.mp4")
    _touch(tmp_path / "B.MP4")
    _touch(tmp_path / "notes.txt")
    listing = fs_browser.list_directory(str(tmp_path), default=tmp_path)
    # Sorted case-insensitively ("a" before "B"), and notes.txt is filtered out entirely.
    assert [e.name for e in listing.entries] == ["a.mp4", "B.MP4"]


def test_list_directory_recognizes_every_supported_extension(tmp_path: Path) -> None:
    for ext in fs_browser.VIDEO_EXTENSIONS:
        _touch(tmp_path / f"clip{ext}")
    listing = fs_browser.list_directory(str(tmp_path), default=tmp_path)
    expected = {f"clip{ext}" for ext in fs_browser.VIDEO_EXTENSIONS}
    assert {e.name for e in listing.entries} == expected


def test_list_directory_lists_directories_first_then_files_by_name(tmp_path: Path) -> None:
    _touch(tmp_path / "c.mp4")
    _touch(tmp_path / "a.mp4")
    (tmp_path / "z-folder").mkdir()
    (tmp_path / "b-folder").mkdir()
    listing = fs_browser.list_directory(str(tmp_path), default=tmp_path)
    assert [e.name for e in listing.entries] == ["b-folder", "z-folder", "a.mp4", "c.mp4"]
    assert [e.is_dir for e in listing.entries] == [True, True, False, False]


def test_list_directory_uses_the_default_when_no_path_given(tmp_path: Path) -> None:
    _touch(tmp_path / "clip.mp4")
    listing = fs_browser.list_directory(None, default=tmp_path)
    assert listing.path == str(tmp_path.resolve())
    assert [e.name for e in listing.entries] == ["clip.mp4"]

    listing = fs_browser.list_directory("", default=tmp_path)
    assert listing.path == str(tmp_path.resolve())


def test_list_directory_entries_carry_absolute_paths(tmp_path: Path) -> None:
    _touch(tmp_path / "clip.mp4")
    listing = fs_browser.list_directory(str(tmp_path), default=tmp_path)
    assert listing.entries[0].path == str((tmp_path / "clip.mp4").resolve())


def test_list_directory_rejects_a_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no such directory"):
        fs_browser.list_directory(str(tmp_path / "does-not-exist"), default=tmp_path)


def test_list_directory_rejects_a_file_path(tmp_path: Path) -> None:
    _touch(tmp_path / "clip.mp4")
    with pytest.raises(ValueError, match="not a directory"):
        fs_browser.list_directory(str(tmp_path / "clip.mp4"), default=tmp_path)


def test_list_directory_reports_no_parent_at_the_filesystem_root() -> None:
    listing = fs_browser.list_directory("/", default=Path("/"))
    assert listing.parent is None


def test_list_directory_reports_the_parent_directory(tmp_path: Path) -> None:
    child = tmp_path / "child"
    child.mkdir()
    listing = fs_browser.list_directory(str(child), default=tmp_path)
    assert listing.parent == str(tmp_path.resolve())


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses directory permission bits")
def test_list_directory_rejects_an_unreadable_directory(tmp_path: Path) -> None:
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o000)
    try:
        with pytest.raises(ValueError, match="cannot list directory"):
            fs_browser.list_directory(str(locked), default=tmp_path)
    finally:
        locked.chmod(0o755)


def test_resolve_video_path_accepts_an_absolute_path(tmp_path: Path) -> None:
    _touch(tmp_path / "clip.mp4")
    resolved = fs_browser.resolve_video_path(str(tmp_path / "clip.mp4"))
    assert resolved == (tmp_path / "clip.mp4").resolve()


def test_resolve_video_path_rejects_an_empty_path() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        fs_browser.resolve_video_path("")


def test_resolve_video_path_rejects_a_relative_path() -> None:
    with pytest.raises(ValueError, match="must be absolute"):
        fs_browser.resolve_video_path("clip.mp4")


def test_resolve_video_path_rejects_a_non_video_extension(tmp_path: Path) -> None:
    _touch(tmp_path / "notes.txt")
    with pytest.raises(ValueError, match="not a recognized video extension"):
        fs_browser.resolve_video_path(str(tmp_path / "notes.txt"))


def test_resolve_video_path_rejects_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no such video"):
        fs_browser.resolve_video_path(str(tmp_path / "missing.mp4"))


def test_resolve_video_path_rejects_a_directory(tmp_path: Path) -> None:
    directory = tmp_path / "dir.mp4"
    directory.mkdir()
    with pytest.raises(ValueError, match="not a file"):
        fs_browser.resolve_video_path(str(directory))
