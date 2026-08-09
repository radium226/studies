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

        Not `display-update`: weston never opens the Display Control channel,
        so guacd's mid-session PDU ends the connection rather than resizing it.
        """
        assert Settings().connection_parameters()["resize-method"] == "reconnect"

    def test_sends_no_password(self):
        """weston has no accounts, so there is no credential to protect."""
        assert "password" not in Settings().connection_parameters()

    def test_names_the_keymap_weston_was_configured_with(self):
        """The two have to agree, and nothing at runtime checks that they do.

        guacd translates keysyms through this keymap and falls back to an RDP
        unicode event for whatever it does not cover -- which weston logs and
        drops. A mismatch here is a keyboard that types some characters and
        silently ignores others.
        """
        settings = Settings(rdp_server_layout="en-us-qwerty")

        assert settings.connection_parameters()["server-layout"] == "en-us-qwerty"

    def test_accepts_the_self_signed_certificate(self):
        parameters = Settings().connection_parameters()

        # Generated at image build, so it is self-signed by construction.
        # Named rather than `any`: weston does not do NLA, so trying it first
        # only buys a failed round trip.
        assert parameters["security"] == "tls"
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

    def test_reads_the_session_settings_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("RDP_USERNAME", "from-env")
        monkeypatch.setenv("RDP_SERVER_LAYOUT", "also-from-env")

        settings = Settings.from_env()

        assert settings.rdp_username == "from-env"
        assert settings.rdp_server_layout == "also-from-env"

    def test_the_defaults_survive_an_empty_environment(self, monkeypatch):
        """`slots=True` turns every class attribute into a slot descriptor.

        Reading the fallbacks off the class rather than off an instance sends
        guacd a protocol named `<member 'remote_protocol' of 'Settings'
        objects>`, which it answers with "Support for protocol ... is not
        installed" -- and only when a variable is unset, which is to say only
        outside the container.
        """
        for name in ("GUACD_HOST", "REMOTE_PROTOCOL", "RDP_USERNAME", "RDP_SERVER_LAYOUT"):
            monkeypatch.delenv(name, raising=False)

        settings = Settings.from_env()

        assert settings.remote_protocol == "rdp"
        assert settings.guacd_host == "127.0.0.1"
        assert isinstance(settings.rdp_server_layout, str)
