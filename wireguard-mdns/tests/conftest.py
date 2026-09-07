"""Shared fixtures for the integration test suite.

Every `integration`-marked test assumes the Vagrant VMs from this study
are already up (`vagrant up` from wireguard-mdns/) -- these tests verify
the live, already-provisioned infrastructure; pytest doesn't stand it up
or tear it down itself (that takes several minutes and is destructive to
run repeatedly).
"""
import grp
import os
import subprocess
from pathlib import Path

import pytest

STUDY_DIR = Path(__file__).resolve().parent.parent
MESH_HOSTS = ["server", "client1", "client2", "foreign"]


def _needs_libvirt_group_workaround() -> bool:
    """True if this process isn't in the libvirt group yet.

    Vagrant/libvirt commands need `libvirt` group membership, which only
    takes effect after a fresh login. Until then (e.g. the same shell
    session that just ran `usermod -aG libvirt`), fall back to running
    the command as that group explicitly.
    """
    try:
        libvirt_gid = grp.getgrnam("libvirt").gr_gid
    except KeyError:
        return False
    return libvirt_gid not in os.getgroups()


def _vagrant_command(*args: str) -> list[str]:
    base = ["vagrant", *args]
    if _needs_libvirt_group_workaround():
        return ["sudo", "-g", "libvirt", "-u", os.environ["USER"], *base]
    return base


def _run_vagrant_ssh(host: str, command: str, timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        _vagrant_command("ssh", host, "-c", command),
        cwd=STUDY_DIR,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def vagrant_ssh(host: str, command: str, timeout: int = 15) -> str:
    """Run `command` on `host` over SSH and return its stdout.

    Fails the test (via assert) if the command's exit status is
    non-zero, with stdout/stderr attached -- most tests just want the
    output and a guarantee the command actually succeeded.
    """
    result = _run_vagrant_ssh(host, command, timeout)
    # vagrant-libvirt logs an unrelated fog gem warning on every
    # invocation; strip it so tests can match on real command output only.
    stdout = "\n".join(
        line for line in result.stdout.splitlines() if "[fog][WARNING]" not in line
    )
    assert result.returncode == 0, (
        f"vagrant ssh {host} -c {command!r} failed (exit {result.returncode}):\n"
        f"stdout: {stdout}\nstderr: {result.stderr}"
    )
    return stdout


@pytest.fixture(scope="session")
def ssh():
    """Callable `ssh(host, command)` fixture for integration tests.

    Skips the whole integration suite up front, with a clear message,
    if the VMs aren't all running -- rather than every test failing
    individually with a confusing connection error.
    """
    result = subprocess.run(
        _vagrant_command("status"), cwd=STUDY_DIR, capture_output=True, text=True, timeout=15
    )
    running = result.stdout.count("running (libvirt)")
    if running < len(MESH_HOSTS):
        pytest.skip(
            f"Only {running}/{len(MESH_HOSTS)} VMs are running -- "
            "run `vagrant up` in wireguard-mdns/ first.\n" + result.stdout
        )
    return vagrant_ssh


@pytest.fixture
def browse(ssh):
    """Callable `browse(host)` fixture: runs `avahi-browse -atp` on `host`
    and returns the set of (interface, service_name, service_type) it
    reports -- one entry per distinct service instance, regardless of
    how many address families (IPv4/IPv6) it was announced over.

    Two format quirks handled here: avahi-browse -p's "type" field is a
    human-readable description ("SSH Remote Terminal"), not the raw
    _ssh._tcp string; and it escapes spaces in names as "\\032" (RFC
    1035-style decimal byte escapes), unescaped back to plain spaces.
    """

    def _browse(host: str) -> set[tuple[str, str, str]]:
        output = ssh(host, "timeout 6 avahi-browse -atp")
        services = set()
        for line in output.splitlines():
            if not line.startswith("+;"):
                continue
            _, iface, _family, name, service_type, _domain = line.split(";")
            services.add((iface, name.replace(r"\032", " "), service_type))
        return services

    return _browse
