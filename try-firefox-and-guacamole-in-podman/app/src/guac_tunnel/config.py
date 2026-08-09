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

    #: The remote desktop guacd should proxy for us. VNC, and 5900, because
    #: what serves the session is wayvnc -- see the README for why this study
    #: went back to the protocol it once left.
    remote_protocol: str = "vnc"
    remote_host: str = "127.0.0.1"
    remote_port: int = 5900

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
        # Through an instance, not through `cls`: `slots=True` replaces every
        # class attribute with a slot descriptor, so `cls.remote_protocol` is
        # not "rdp" but a `member_descriptor` -- which then travels all the way
        # to guacd as the protocol name, and comes back as "Support for
        # protocol "<member 'remote_protocol' of 'Settings' objects>" is not
        # installed". Only reachable with the variable unset, which is to say
        # only outside the container.
        fallback = cls()
        return cls(
            guacd_host=_env("GUACD_HOST", fallback.guacd_host),
            guacd_port=_env_int("GUACD_PORT", fallback.guacd_port),
            remote_protocol=_env("REMOTE_PROTOCOL", fallback.remote_protocol),
            remote_host=_env("REMOTE_HOST", fallback.remote_host),
            remote_port=_env_int("REMOTE_PORT", fallback.remote_port),
            default_width=_env_int("SCREEN_WIDTH", fallback.default_width),
            default_height=_env_int("SCREEN_HEIGHT", fallback.default_height),
            default_dpi=_env_int("SCREEN_DPI", fallback.default_dpi),
        )

    def connection_parameters(self) -> dict[str, str]:
        """Parameters for guacd's VNC client.

        Shorter than the RDP set it replaces, and most of what is missing was
        never doing anything. There is no password, because wayvnc serves the
        session to whoever connects; no security mode to name, because there is
        no TLS to negotiate; and no `server-layout`, because guacd's VNC client
        sends keysyms straight down the RFB wire instead of translating them to
        scancodes through a keymap first.

        There is also no `resize-method`. Client-driven resize is what this
        study wants above everything, it is what sent it to RDP in the first
        place, and in guacd 1.6 the VNC client simply does it -- the only knob
        is `disable-display-resize`, which is off.

        `handshake.py` matches these by name against whatever guacd asked for
        and sends an empty string for anything missing, so a misspelled
        parameter is dropped in silence rather than rejected.
        """
        return {
            "hostname": self.remote_host,
            "port": str(self.remote_port),
            "color-depth": "24",
        }
