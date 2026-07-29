"""Hermetic tests for resolve_direct_media_url: the yt-dlp subprocess is replaced
with a fake, so no yt-dlp binary (or network) is needed — same testing boundary as
the rest of core."""

import asyncio

import pytest

from video_analyzer import core
from video_analyzer.core.yt_dlp_url_resolver import DEFAULT_FORMAT


class _FakeProcess:
    def __init__(self, returncode: int, stdout: bytes, stderr: bytes) -> None:
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr


class _FakeSubprocess:
    """Stand-in for asyncio.create_subprocess_exec that records its argv."""

    def __init__(self, process: _FakeProcess) -> None:
        self._process = process
        self.argv: tuple[str, ...] | None = None

    async def __call__(self, *argv: str, **_kwargs: object) -> _FakeProcess:
        self.argv = argv
        return self._process


async def test_returns_stripped_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeSubprocess(_FakeProcess(0, b"https://cdn.example/video.mp4\n", b""))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake)

    resolved = await core.resolve_direct_media_url("https://example.com/watch?v=abc")

    assert resolved == "https://cdn.example/video.mp4"


async def test_invokes_yt_dlp_with_default_format_and_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeSubprocess(_FakeProcess(0, b"https://cdn.example/video.mp4\n", b""))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake)

    await core.resolve_direct_media_url("https://example.com/watch?v=abc")

    assert fake.argv == (
        "yt-dlp",
        "--format",
        DEFAULT_FORMAT,
        "--get-url",
        "https://example.com/watch?v=abc",
    )


async def test_custom_format_is_passed_through(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeSubprocess(_FakeProcess(0, b"https://cdn.example/video.mp4\n", b""))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake)

    await core.resolve_direct_media_url("https://example.com/w", format="best[height<=480]")

    assert fake.argv is not None
    assert fake.argv[1:3] == ("--format", "best[height<=480]")


async def test_failure_raises_with_return_code_and_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeSubprocess(_FakeProcess(1, b"", b"ERROR: unsupported URL"))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake)

    with pytest.raises(RuntimeError, match=r"yt-dlp failed \(1\).*unsupported URL"):
        await core.resolve_direct_media_url("https://example.com/not-a-video")
