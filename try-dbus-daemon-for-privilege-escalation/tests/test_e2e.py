import subprocess
import time
from contextlib import redirect_stdout
from io import StringIO
from typing import Generator

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


def client(command: Command) -> ExitCode:
    """Helper function to run the client binary with given command."""
    cmd = ["run", "--user"] + command
    result = subprocess.run(cmd)
    return result.returncode


@pytest.mark.e2e
def test_echo_command(daemon, capsys):
    """Test running a simple echo command."""
    exit_code = client(["echo", "foobar"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "foobar" in captured.out