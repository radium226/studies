#!/usr/bin/env python3

from typing import Callable
from subprocess import Popen
import sys
import pty
import os
from enum import StrEnum
import select

import signal

from threading import Thread

class Mode(StrEnum):

    TTY = "tty"
    PIPE = "pipe"


def redirect(from_fd: int, to_fd: int) -> tuple[Callable[[], None], Thread]:
    abort_read_fd, abort_write_fd = os.pipe()

    def transfer():
        while True:
            print("Waiting for data on stdin...")
            r, _, _ = select.select([from_fd, abort_read_fd], [], [])
            if from_fd in r:
                data = os.read(from_fd, 1024)
                if not data:
                    print("EOF reached, closing stdin.")
                    break  # EOF
                print(f"Redirecting stdin (data={data})")
                try:
                    os.write(to_fd, data)
                except OSError as e:
                    print(f"Error writing to stdin: {e}")
                    break
            if abort_read_fd in r:
                print("Abort signal received, stopping redirection.")
                break
        os.close(to_fd)

    thread = Thread(target=transfer)

    def abort():
        print("Aborting redirection...")
        os.write(abort_write_fd, b"abort")
        os.close(abort_write_fd)

    return abort, thread



def app(mode: Mode):
    print(f"Running in {mode} mode... ")

    match mode:
        case Mode.TTY:
            stdin_read_fd, stdin_write_fd = pty.openpty()
        case Mode.PIPE:
            stdin_read_fd, stdin_write_fd = os.pipe()
        case _:
            raise ValueError(f"Unknown mode: {mode}")

    print("Starting witness... ")

    def preexec(): # Don't forward signals.
        os.setpgrp()

    process = Popen(
        ["tr", "a-z", "A-Z"],  # Example command, replace with actual witness command
        stdin=stdin_read_fd,
        stdout=sys.stdout,
        stderr=sys.stderr,
        preexec_fn=preexec,
    )
    os.close(stdin_read_fd)  # Close the read end in the parent process

    
    abort_stdin_redirection, redirect_stdin_thread = redirect(sys.stdin.fileno(), stdin_write_fd)
    redirect_stdin_thread.start()

    def signal_handler(signum, frame):
        print(f"Received signal {signum}, terminating witness...")
        process.send_signal(signal.SIGTERM)

    signal.signal(signal.SIGINT, signal_handler)

    exit_code = process.wait()
    print(f"Witness finished with exit code: {exit_code}")

    abort_stdin_redirection()
    
    if sys.stdin.isatty():
        exit_code = 128 - exit_code
    sys.exit(exit_code)



if __name__ == "__main__":
    mode = Mode(sys.argv[1].lstrip("-")) if len(sys.argv) > 1 else Mode.PIPE
    app(mode)