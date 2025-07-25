#!/usr/bin/env python3

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


def app(mode: Mode):
    print(f"Running in {mode} mode... ")

    done_read_fd, done_write_fd = os.pipe()

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

    def redirect_stdin():
        fd = sys.stdin.fileno()
        while True:
            print("Waiting for data on stdin...")
            r, w, e = select.select([fd, done_read_fd], [], [])
            print(f"Select returned: r={r}, w={w}, e={e}")
            if fd in r:
                data = os.read(sys.stdin.fileno(), 1024)
                if not data:
                    print("EOF reached, closing stdin.")
                    break  # EOF
                print(f"Redirecting stdin (data={data})")
                try:
                    os.write(stdin_write_fd, data)
                except OSError as e:
                    print(f"Error writing to stdin: {e}")
                    break
            if done_read_fd in r:
                print("Done read pipe signaled, closing stdin.")
                break
        os.close(stdin_write_fd)
    
    redirect_thread = Thread(target=redirect_stdin)
    redirect_thread.start()

    def signal_handler(signum, frame):
        print(f"Received signal {signum}, terminating witness...")
        process.send_signal(signal.SIGTERM)

    signal.signal(signal.SIGINT, signal_handler)

    exit_code = process.wait()
    print(f"Witness finished with exit code: {exit_code}")

    os.write(done_write_fd, b"done")

    # for fd in [stdin_write_fd, stdin_read_fd]:
    #     try:
    #         os.close(fd)
    #     except OSError as e:
    #         print(f"Error closing file descriptor {fd}: {e}")
    
    if sys.stdin.isatty():
        exit_code = 128 - exit_code
    sys.exit(exit_code)



if __name__ == "__main__":
    mode = Mode(sys.argv[1].lstrip("-")) if len(sys.argv) > 1 else Mode.PIPE
    app(mode)