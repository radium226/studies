"""Varlink client with file descriptor passing."""

import asyncio
import os
import signal
import sys

from .protocol import Request, Response, read_message
from .socket import create_client_socket, recv_with_fds, send_with_fds


async def relay_fd_to_stream(fd: int, stream) -> None:
    """Relay data from a file descriptor to a stream."""
    loop = asyncio.get_event_loop()
    try:
        while True:
            data = await loop.run_in_executor(None, os.read, fd, 4096)
            if not data:
                break
            stream.buffer.write(data)
            stream.buffer.flush()
    except OSError:
        pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


async def run_client(command: list[str]) -> int:
    """Run a command via the server and return its exit code."""
    sock = create_client_socket()
    loop = asyncio.get_event_loop()

    stdin_fd = os.dup(sys.stdin.fileno())

    request = Request(method="Execute", parameters={"command": command})
    send_with_fds(sock, request.encode(), [stdin_fd])
    os.close(stdin_fd)

    buffer = b""
    data, fds = recv_with_fds(sock)
    buffer += data

    msg_data, buffer = read_message(buffer)
    if msg_data is None:
        print("Protocol error: incomplete message", file=sys.stderr)
        return 1

    response = Response.decode(msg_data)
    if response.error:
        print(f"Server error: {response.error}", file=sys.stderr)
        return 1

    if len(fds) < 2:
        print("Protocol error: expected stdout and stderr FDs", file=sys.stderr)
        return 1

    stdout_fd, stderr_fd = fds[0], fds[1]

    # Set up signal handlers to forward signals to the server
    def send_signal(signum):
        try:
            sig_request = Request(method="Signal", parameters={"signal": int(signum)})
            send_with_fds(sock, sig_request.encode(), [])
        except OSError:
            pass  # Socket may be closed

    loop.add_signal_handler(signal.SIGTERM, lambda: send_signal(signal.SIGTERM))
    loop.add_signal_handler(signal.SIGINT, lambda: send_signal(signal.SIGINT))

    stdout_task = asyncio.create_task(relay_fd_to_stream(stdout_fd, sys.stdout))
    stderr_task = asyncio.create_task(relay_fd_to_stream(stderr_fd, sys.stderr))

    def recv_blocking():
        return recv_with_fds(sock)

    while True:
        data, _ = await loop.run_in_executor(None, recv_blocking)
        if not data:
            break
        buffer += data

        msg_data, buffer = read_message(buffer)
        if msg_data is not None:
            exit_response = Response.decode(msg_data)
            if exit_response.parameters and "exit_code" in exit_response.parameters:
                await stdout_task
                await stderr_task
                loop.remove_signal_handler(signal.SIGTERM)
                loop.remove_signal_handler(signal.SIGINT)
                sock.close()
                return exit_response.parameters["exit_code"]

    await stdout_task
    await stderr_task
    loop.remove_signal_handler(signal.SIGTERM)
    loop.remove_signal_handler(signal.SIGINT)
    sock.close()
    return 1


def main():
    """Entry point for study."""
    if len(sys.argv) < 2:
        print("Usage: study <command> [args...]", file=sys.stderr)
        sys.exit(1)

    command = sys.argv[1:]
    try:
        exit_code = asyncio.run(run_client(command))
        sys.exit(exit_code)
    except FileNotFoundError:
        print("Error: Server not running (socket not found)", file=sys.stderr)
        sys.exit(1)
    except ConnectionRefusedError:
        print("Error: Connection refused", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
