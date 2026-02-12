"""Unix socket utilities with SCM_RIGHTS file descriptor passing."""

import array
import socket
import struct

SOCKET_PATH = "/tmp/studyd.sock"
MAX_FDS = 16
CMSG_SIZE = socket.CMSG_LEN(MAX_FDS * 4)


def send_with_fds(sock: socket.socket, data: bytes, fds: list[int]) -> None:
    """Send data with file descriptors using SCM_RIGHTS."""
    if fds:
        fd_array = array.array("i", fds)
        ancdata = [(socket.SOL_SOCKET, socket.SCM_RIGHTS, fd_array)]
        sock.sendmsg([data], ancdata)
    else:
        sock.sendall(data)


def recv_with_fds(
    sock: socket.socket, bufsize: int = 4096
) -> tuple[bytes, list[int]]:
    """Receive data with file descriptors using SCM_RIGHTS."""
    data, ancdata, flags, addr = sock.recvmsg(bufsize, CMSG_SIZE)
    fds: list[int] = []
    for level, type_, fd_bytes in ancdata:
        if level == socket.SOL_SOCKET and type_ == socket.SCM_RIGHTS:
            num_fds = len(fd_bytes) // 4
            fds.extend(struct.unpack(f"{num_fds}i", fd_bytes))
    return data, fds


def create_server_socket() -> socket.socket:
    """Create and bind a Unix domain socket for the server."""
    import os

    if os.path.exists(SOCKET_PATH):
        os.unlink(SOCKET_PATH)

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(SOCKET_PATH)
    sock.listen(5)
    sock.setblocking(False)
    return sock


def create_client_socket() -> socket.socket:
    """Create and connect a Unix domain socket for the client."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(SOCKET_PATH)
    return sock
