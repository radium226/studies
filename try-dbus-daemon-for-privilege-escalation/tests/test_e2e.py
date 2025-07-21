import subprocess
import time
from contextlib import redirect_stdout
from io import StringIO
from typing import Generator
import sys

import pytest

from radium226.run.shared.types import Command, ExitCode


@pytest.fixture
def daemon() -> Generator[subprocess.Popen, None, None]:
    """Start the rund daemon process."""
    daemon_process = subprocess.Popen(["rund", "--user"])
    
    # Give the daemon time to start up
    time.sleep(3)
    
    try:
        yield daemon_process
    finally:
        daemon_process.terminate()
        daemon_process.wait()


def client(command: Command) -> tuple[ExitCode, str, str]:
    """Helper function to run the client binary with given command."""
    cmd = ["run", "--user"] + command
    result = subprocess.run(cmd, capture_output=True, text=True)
    return (result.returncode, result.stdout, result.stderr)


@pytest.mark.e2e
def test_echo_command(daemon):
    # """Test running a simple echo command."""
    exit_code, client_stdout, _ = client(["echo", "foobar"])
    # print("foobar")
    assert exit_code == 0
    assert "foobar" in client_stdout