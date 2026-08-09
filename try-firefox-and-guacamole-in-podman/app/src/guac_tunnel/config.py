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
    remote_protocol: str = "rdp"
    remote_host: str = "127.0.0.1"
    remote_port: int = 3389

    #: What xrdp's session manager authenticates. Must match the credential the
    #: Firefox container sets on its session user.
    rdp_username: str = "firefox"
    rdp_password: str = "firefox"

    #: Fallback geometry, used when the browser does not say what it wants.
    #: The dpi is 96 deliberately: guacd's RDP client treats it as a divisor
    #: and rescales the requested pixels by 96/dpi, so anything else quietly
    #: asks for a different session than the one named here.
    default_width: int = 412
    default_height: int = 915
    default_dpi: int = 96

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
            rdp_username=_env("RDP_USERNAME", cls.rdp_username),
            rdp_password=_env("RDP_PASSWORD", cls.rdp_password),
            default_width=_env_int("SCREEN_WIDTH", cls.default_width),
            default_height=_env_int("SCREEN_HEIGHT", cls.default_height),
            default_dpi=_env_int("SCREEN_DPI", cls.default_dpi),
        )

    def connection_parameters(self) -> dict[str, str]:
        """Parameters for guacd's RDP client.

        There is a password, unlike the Xvnc setup: xrdp authenticates through
        PAM and has no equivalent of `-SecurityTypes None`. It is a fixed
        credential rather than a secret, which is only acceptable because 3389
        never leaves the pod -- see the README.

        `handshake.py` matches these by name against whatever guacd asked for
        and sends an empty string for anything missing, so a misspelled
        parameter is dropped in silence rather than rejected. `resize-method`
        is the one that would hurt: without it the session simply never
        resizes, and nothing anywhere says why.
        """
        return {
            "hostname": self.remote_host,
            "port": str(self.remote_port),
            "username": self.rdp_username,
            "password": self.rdp_password,
            # The whole reason for preferring RDP: guacd turns a mid-session
            # `size` instruction into a Display Control PDU, xorgxrdp does a
            # RandR resize, and Firefox reflows.
            "resize-method": "display-update",
            # xrdp negotiates TLS by default with a certificate generated at
            # image build, so it is self-signed by construction.
            "security": "any",
            "ignore-cert": "true",
            "color-depth": "24",
            # There is no sound device in the container and no chansrv audio
            # path; asking for one only buys a channel that never carries.
            "disable-audio": "true",
        }
