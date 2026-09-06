"""The connection definition, which is the whole of what the webapp would hold.

Nothing here checks guacd's behaviour; it checks that we asked for the right
things. That distinction matters because the handshake matches parameters by
name and sends an empty string for anything guacd did not ask for, so a
parameter that is misspelled -- or left behind from the RDP setup -- is dropped
without an error anywhere. These tests are the only thing that would notice.
"""

from __future__ import annotations

import pytest

from guac_tunnel.config import Settings


class TestConnectionParameters:
    def test_asks_guacd_for_no_resize_of_its_own(self):
        """Resizing is not guacd's job here, and must not become it.

        guacd cannot change the size of a live VNC connection -- it stops at
        `Screen data has not been initialized, yet` -- so the session is
        reshaped through sway between two connections instead. A stray
        `resize-method` would be the RDP setup leaking back in.
        """
        assert "resize-method" not in Settings().connection_parameters()

    def test_sends_no_credentials(self):
        """wayvnc serves the session to whoever connects, so there are none."""
        parameters = Settings().connection_parameters()

        assert "password" not in parameters
        assert "username" not in parameters

    def test_points_at_the_configured_remote(self):
        settings = Settings(remote_host="10.0.0.5", remote_port=5901)
        parameters = settings.connection_parameters()

        assert parameters["hostname"] == "10.0.0.5"
        assert parameters["port"] == "5901"

    @pytest.mark.parametrize("name", ["security", "ignore-cert", "server-layout", "disable-audio"])
    def test_no_rdp_parameters_survive(self, name):
        """Left in place these would be sent to guacd's VNC client and ignored."""
        assert name not in Settings().connection_parameters()


class TestDefaults:
    def test_the_fallback_dpi_is_96(self):
        """96 is not a display property here, it is an instruction to guacd.

        guacd rescales the requested pixels by 96/dpi, so any other value
        silently asks for a different session than the one configured.
        """
        assert Settings().default_dpi == 96

    def test_reads_the_session_control_path_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("SESSION_SOCKET", "/somewhere/else.sock")
        monkeypatch.setenv("SESSION_OUTPUT", "OTHER-1")

        settings = Settings.from_env()

        assert settings.session_socket == "/somewhere/else.sock"
        assert settings.session_output == "OTHER-1"

    def test_the_defaults_survive_an_empty_environment(self, monkeypatch):
        """`slots=True` turns every class attribute into a slot descriptor.

        Reading the fallbacks off the class rather than off an instance sends
        guacd a protocol named `<member 'remote_protocol' of 'Settings'
        objects>`, which it answers with "Support for protocol ... is not
        installed" -- and only when a variable is unset, which is to say only
        outside the container.
        """
        for name in ("GUACD_HOST", "REMOTE_PROTOCOL", "SESSION_SOCKET", "SESSION_OUTPUT"):
            monkeypatch.delenv(name, raising=False)

        settings = Settings.from_env()

        assert settings.remote_protocol == "vnc"
        assert settings.guacd_host == "127.0.0.1"
        assert settings.session_output == "HEADLESS-1"
