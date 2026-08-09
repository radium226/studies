"""Server side of the "choose a file from anywhere" browse modal.

Unlike a directory allowlist, this deliberately has no access boundary beyond "must be a real,
readable directory/file" — the whole point is letting the operator navigate anywhere the process
can read and pick any file with a recognized video extension. That's an acceptable posture only
because this is a local single-user tool (see the repo root CLAUDE.md); don't reuse this module
in a context with untrusted clients.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".mkv", ".avi", ".webm", ".wmv"})


@dataclass(frozen=True, slots=True)
class DirectoryEntry:
    name: str
    path: str
    is_dir: bool
    size: int | None
    modified: float | None


@dataclass(frozen=True, slots=True)
class DirectoryListing:
    path: str
    parent: str | None
    entries: list[DirectoryEntry]


def list_directory(path: str | None, *, default: Path) -> DirectoryListing:
    """List the directories and recognized video files directly under `path` (or `default` if
    `path` is empty/None — the browse modal's initial directory, opened with no query yet).

    Raises `ValueError` if the path doesn't resolve to a readable, existing directory.
    """
    directory = Path(path).expanduser() if path else default
    try:
        directory = directory.resolve(strict=True)
    except OSError as error:
        raise ValueError(f"no such directory: {path!r}") from error
    if not directory.is_dir():
        raise ValueError(f"not a directory: {path!r}")

    try:
        children = list(directory.iterdir())
    except OSError as error:
        raise ValueError(f"cannot list directory: {directory}") from error

    entries: list[DirectoryEntry] = []
    for child in children:
        try:
            is_dir = child.is_dir()
        except OSError:
            continue  # broken symlink, permission error, etc. mid-listing - skip it
        if is_dir:
            entries.append(
                DirectoryEntry(
                    name=child.name, path=str(child), is_dir=True, size=None, modified=None
                )
            )
        elif child.suffix.lower() in VIDEO_EXTENSIONS:
            try:
                stat = child.stat()
            except OSError:
                continue
            entries.append(
                DirectoryEntry(
                    name=child.name,
                    path=str(child),
                    is_dir=False,
                    size=stat.st_size,
                    modified=stat.st_mtime,
                )
            )

    entries.sort(key=lambda entry: (not entry.is_dir, entry.name.lower()))
    parent = str(directory.parent) if directory.parent != directory else None
    return DirectoryListing(path=str(directory), parent=parent, entries=entries)


def resolve_video_path(path_str: str) -> Path:
    """Resolve an absolute path picked in the browse modal to an existing video file.

    Raises `ValueError` if `path_str` isn't absolute, doesn't have a recognized video extension,
    or doesn't resolve to an existing file.
    """
    if not path_str:
        raise ValueError("path must not be empty")
    candidate = Path(path_str).expanduser()
    if not candidate.is_absolute():
        raise ValueError(f"path must be absolute: {path_str!r}")
    if candidate.suffix.lower() not in VIDEO_EXTENSIONS:
        raise ValueError(f"not a recognized video extension: {path_str!r}")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise ValueError(f"no such video: {path_str!r}") from error
    if not resolved.is_file():
        raise ValueError(f"not a file: {path_str!r}")
    return resolved
