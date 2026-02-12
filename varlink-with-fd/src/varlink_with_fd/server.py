"""Varlink server with file descriptor passing."""

import asyncio
import os
import select
import signal
import socket
import subprocess
import sys
import threading

from .protocol import Request, Response, read_message
from .socket import (
    SOCKET_PATH,
    recv_with_fds,
    send_with_fds,
)

# Global tracking of running processes
running_processes: dict[int, list[str]] = {}
processes_lock = threading.Lock()


def handle_client_sync(client_sock: socket.socket):
    """Handle a single client connection synchronously."""
    buffer = b""

    try:
        data, fds = recv_with_fds(client_sock)
        if not data:
            return
        buffer += data

        msg_data, buffer = read_message(buffer)
        if msg_data is None:
            return

        request = Request.decode(msg_data)

        if request.method != "Execute":
            response = Response(error=f"Unknown method: {request.method}")
            send_with_fds(client_sock, response.encode(), [])
            return

        command = request.parameters.get("command", [])
        if not command:
            response = Response(error="No command specified")
            send_with_fds(client_sock, response.encode(), [])
            return

        stdin_fd = fds[0] if fds else os.open("/dev/null", os.O_RDONLY)

        stdout_read, stdout_write = os.pipe()
        stderr_read, stderr_write = os.pipe()

        try:
            process = subprocess.Popen(
                command,
                stdin=stdin_fd,
                stdout=stdout_write,
                stderr=stderr_write,
                close_fds=True,
            )
        except Exception as e:
            os.close(stdout_read)
            os.close(stdout_write)
            os.close(stderr_read)
            os.close(stderr_write)
            os.close(stdin_fd)
            response = Response(error=str(e))
            send_with_fds(client_sock, response.encode(), [])
            return

        os.close(stdout_write)
        os.close(stderr_write)
        os.close(stdin_fd)

        with processes_lock:
            running_processes[process.pid] = command

        response = Response(parameters={})
        send_with_fds(client_sock, response.encode(), [stdout_read, stderr_read])

        os.close(stdout_read)
        os.close(stderr_read)

        # Wait for process while listening for Signal requests from client
        signal_buffer = b""
        while True:
            readable, _, _ = select.select([client_sock], [], [], 0.1)

            if readable:
                try:
                    data, _ = recv_with_fds(client_sock)
                    if data:
                        signal_buffer += data
                        msg_data, signal_buffer = read_message(signal_buffer)
                        if msg_data:
                            req = Request.decode(msg_data)
                            if req.method == "Signal":
                                sig = req.parameters.get("signal", signal.SIGTERM)
                                print(f"Forwarding signal {sig} to process {process.pid}", file=sys.stderr)
                                process.send_signal(sig)
                except OSError:
                    pass

            ret = process.poll()
            if ret is not None:
                exit_code = ret
                print(f"Process {process.pid} exited with code {exit_code}", file=sys.stderr)
                break

        with processes_lock:
            running_processes.pop(process.pid, None)

        exit_response = Response(parameters={"exit_code": exit_code})
        send_with_fds(client_sock, exit_response.encode(), [])

    except Exception as e:
        print(f"Error handling client: {e}", file=sys.stderr)
    finally:
        client_sock.close()


async def log_running_processes(stop_event: asyncio.Event):
    """Log running processes every second."""
    while not stop_event.is_set():
        with processes_lock:
            if running_processes:
                procs = ", ".join(
                    f"{pid}:{cmd[0]}" for pid, cmd in running_processes.items()
                )
                print(f"Running: {procs}", file=sys.stderr)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            pass


async def run_server():
    """Run the varlink server."""
    if os.path.exists(SOCKET_PATH):
        os.unlink(SOCKET_PATH)

    server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server_sock.bind(SOCKET_PATH)
    server_sock.listen(5)
    server_sock.setblocking(False)

    print(f"Server listening on {SOCKET_PATH}", file=sys.stderr)

    loop = asyncio.get_event_loop()
    stop_event = asyncio.Event()

    def handle_signal():
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, handle_signal)

    logging_task = asyncio.create_task(log_running_processes(stop_event))

    try:
        while not stop_event.is_set():
            try:
                client_sock, _ = await asyncio.wait_for(
                    loop.sock_accept(server_sock), timeout=0.5
                )
                loop.run_in_executor(None, handle_client_sync, client_sock)
            except asyncio.TimeoutError:
                continue
    finally:
        logging_task.cancel()
        server_sock.close()
        if os.path.exists(SOCKET_PATH):
            os.unlink(SOCKET_PATH)


def main():
    """Entry point for studyd."""
    try:
        asyncio.run(run_server())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
