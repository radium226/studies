"""Environment-driven settings.

Everything here has a working default, so `uv run guac-tunnel` does something
useful without a container around it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

STATIC_ROOT = Path(__file__).parent / "static"


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from error


@dataclass(frozen=True, slots=True)
class Settings:
    """How to reach guacd, and what to tell it to connect to."""

    #: Where guacd listens. Inside the pod this is always loopback.
    guacd_host: str = "127.0.0.1"
    guacd_port: int = 4822

    #: The remote desktop guacd should proxy for us.
    remote_protocol: str = "vnc"
    remote_host: str = "127.0.0.1"
    remote_port: int = 5900

    #: Fallback geometry, used when the browser does not say what it wants.
    default_width: int = 412
    default_height: int = 915
    default_dpi: int = 192

    #: Clamp what a client may ask for, so a bad query string cannot ask guacd
    #: to allocate an absurd framebuffer.
    max_dimension: int = 4096

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            guacd_host=_env("GUACD_HOST", cls.guacd_host),
            guacd_port=_env_int("GUACD_PORT", cls.guacd_port),
            remote_protocol=_env("REMOTE_PROTOCOL", cls.remote_protocol),
            remote_host=_env("REMOTE_HOST", cls.remote_host),
            remote_port=_env_int("REMOTE_PORT", cls.remote_port),
            default_width=_env_int("SCREEN_WIDTH", cls.default_width),
            default_height=_env_int("SCREEN_HEIGHT", cls.default_height),
            default_dpi=_env_int("SCREEN_DPI", cls.default_dpi),
        )

    def connection_parameters(self) -> dict[str, str]:
        """Parameters for guacd's VNC client.

        No password: Xvnc runs with `-SecurityTypes None`, which is only
        acceptable because port 5900 never leaves the pod.
        """
        return {
            "hostname": self.remote_host,
            "port": str(self.remote_port),
            "color-depth": "24",
            "cursor": "remote",
            "autoretry": "3",
        }
