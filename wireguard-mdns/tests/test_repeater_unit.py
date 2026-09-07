"""Unit tests for mdns-unicast-repeater's forwarding logic.

No VMs, no sockets, no network involved -- just the pure decision of
"which peers should this packet be repeated to", loaded directly from
the deployed script by path so there's no risk of testing a copy that's
drifted from what's actually installed on the server.
"""
import importlib.util
import sys
import threading
import time
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

REPEATER_PATH = Path(__file__).resolve().parent.parent / "ansible" / "files" / "mdns-unicast-repeater"


def _load_repeater_module():
    # No .py suffix, so spec_from_file_location can't infer a loader from
    # the extension the way it would for an ordinary module -- give it one
    # explicitly instead.
    loader = SourceFileLoader("mdns_unicast_repeater", str(REPEATER_PATH))
    spec = importlib.util.spec_from_file_location(loader.name, REPEATER_PATH, loader=loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


repeater = _load_repeater_module()


class TestForwardTargets:
    def test_excludes_the_sender(self):
        assert repeater.forward_targets(["10.0.0.2", "10.0.0.3"], "10.0.0.2") == ["10.0.0.3"]

    def test_forwards_to_all_other_peers(self):
        peers = ["10.0.0.2", "10.0.0.3", "10.0.0.4"]
        assert repeater.forward_targets(peers, "10.0.0.2") == ["10.0.0.3", "10.0.0.4"]

    def test_sender_not_in_peer_list_forwards_to_everyone(self):
        # e.g. a packet originating from the hub itself, which isn't one
        # of its own spoke peers.
        peers = ["10.0.0.2", "10.0.0.3"]
        assert repeater.forward_targets(peers, "10.0.0.1") == peers

    def test_single_peer_never_forwards_to_itself(self):
        assert repeater.forward_targets(["10.0.0.2"], "10.0.0.2") == []

    def test_empty_peer_list(self):
        assert repeater.forward_targets([], "10.0.0.2") == []

    def test_works_for_ipv6_addresses_too(self):
        peers = ["fd00::2", "fd00::3"]
        assert repeater.forward_targets(peers, "fd00::2") == ["fd00::3"]

    def test_preserves_peer_list_order(self):
        peers = ["10.0.0.4", "10.0.0.3", "10.0.0.2"]
        assert repeater.forward_targets(peers, "10.0.0.3") == ["10.0.0.4", "10.0.0.2"]

    def test_does_not_mutate_input_list(self):
        peers = ["10.0.0.2", "10.0.0.3"]
        original = list(peers)
        repeater.forward_targets(peers, "10.0.0.2")
        assert peers == original


class TestMain:
    def test_reads_config_from_environment(self, monkeypatch):
        """main() wires env vars to repeat_v4/repeat_v6 -- verified by
        substituting both with fakes and checking what they were called
        with, since the real ones block forever on a socket recv loop.
        """
        calls = {}

        # Block after recording the call, like the real repeat_v4/v6 do
        # (they never return under normal operation) -- otherwise
        # run_forever's retry loop would call these again immediately,
        # spinning a tight loop in a background thread for the rest of
        # the test session.
        def fake_repeat_v4(local_v4, peers_v4):
            calls["v4"] = (local_v4, peers_v4)
            threading.Event().wait()

        def fake_repeat_v6(iface, peers_v6):
            calls["v6"] = (iface, peers_v6)
            threading.Event().wait()

        monkeypatch.setattr(repeater, "repeat_v4", fake_repeat_v4)
        monkeypatch.setattr(repeater, "repeat_v6", fake_repeat_v6)
        monkeypatch.setenv("MDNS_REPEATER_IFACE", "wg0")
        monkeypatch.setenv("MDNS_REPEATER_LOCAL_V4", "10.0.0.1")
        monkeypatch.setenv("MDNS_REPEATER_PEERS_V4", "10.0.0.2,10.0.0.3")
        monkeypatch.setenv("MDNS_REPEATER_PEERS_V6", "fd00::2,fd00::3")

        # main() itself blocks forever (threading.Event().wait()) once
        # its worker threads are started, same as the fakes above -- run
        # it in a background thread too, then give everything a moment
        # to run before asserting.
        threading.Thread(target=repeater.main, daemon=True).start()
        time.sleep(0.5)

        assert calls["v4"] == ("10.0.0.1", ["10.0.0.2", "10.0.0.3"])
        assert calls["v6"] == ("wg0", ["fd00::2", "fd00::3"])

    def test_missing_required_env_var_raises(self, monkeypatch):
        monkeypatch.delenv("MDNS_REPEATER_IFACE", raising=False)
        with pytest.raises(KeyError):
            repeater.main()
