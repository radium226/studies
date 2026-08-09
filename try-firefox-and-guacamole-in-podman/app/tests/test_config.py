"""The connection definition, which is the whole of what the webapp would hold.

Nothing here checks guacd's behaviour; it checks that we asked for the right
things. That distinction matters because the handshake matches parameters by
name and sends an empty string for anything guacd did not ask for, so a
parameter that is misspelled -- or left behind from the VNC setup -- is dropped
without an error anywhere. These tests are the only thing that would notice.
"""

from __future__ import annotations

import pytest

from guac_tunnel.config import Settings


class TestConnectionParameters:
    def test_asks_for_dynamic_resize(self):
        """The reason this study speaks RDP at all.

        Without it the session simply never changes shape, and rotating the
        phone goes back to letterboxing with nothing to say why.
        """
        assert Settings().connection_parameters()["resize-method"] == "display-update"

    def test_carries_the_session_credentials(self):
        settings = Settings(rdp_username="someone", rdp_password="secret")
        parameters = settings.connection_parameters()

        # xrdp authenticates through PAM; there is no passwordless mode.
        assert parameters["username"] == "someone"
        assert parameters["password"] == "secret"

    def test_accepts_the_self_signed_certificate(self):
        parameters = Settings().connection_parameters()

        # Generated at image build, so it is self-signed by construction.
        assert parameters["security"] == "any"
        assert parameters["ignore-cert"] == "true"

    def test_points_at_the_configured_remote(self):
        settings = Settings(remote_host="10.0.0.5", remote_port=3390)
        parameters = settings.connection_parameters()

        assert parameters["hostname"] == "10.0.0.5"
        assert parameters["port"] == "3390"

    @pytest.mark.parametrize("name", ["cursor", "autoretry", "swap-red-blue"])
    def test_no_vnc_parameters_survive(self, name):
        """Left in place these would be sent to guacd's RDP client and ignored."""
        assert name not in Settings().connection_parameters()


class TestDefaults:
    def test_the_fallback_dpi_is_96(self):
        """96 is not a display property here, it is an instruction to guacd.

        Its RDP client rescales the requested pixels by 96/dpi, so any other
        value silently asks for a different session than the one configured.
        """
        assert Settings().default_dpi == 96

    def test_reads_the_credentials_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("RDP_USERNAME", "from-env")
        monkeypatch.setenv("RDP_PASSWORD", "also-from-env")

        settings = Settings.from_env()

        assert settings.rdp_username == "from-env"
        assert settings.rdp_password == "also-from-env"
