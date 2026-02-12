import os
import socket
import tempfile

from varlink_with_fd.socket import (
    create_client_socket,
    create_server_socket,
    recv_with_fds,
    send_with_fds,
)


# send_with_fds / recv_with_fds tests


def test_send_recv_data_only():
    """Send/receive data without file descriptors."""
    sock1, sock2 = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        data = b"hello world"
        send_with_fds(sock1, data, [])
        received, fds = recv_with_fds(sock2)
        assert received == data
        assert fds == []
    finally:
        sock1.close()
        sock2.close()


def test_send_recv_with_single_fd():
    """Send/receive data with one file descriptor."""
    sock1, sock2 = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        # Create a temporary file to get a real fd
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"test content")
            tmp_path = tmp.name

        fd = os.open(tmp_path, os.O_RDONLY)
        try:
            data = b"message with fd"
            send_with_fds(sock1, data, [fd])
            received, received_fds = recv_with_fds(sock2)

            assert received == data
            assert len(received_fds) == 1

            # Verify the received fd is valid by reading from it
            content = os.read(received_fds[0], 100)
            assert content == b"test content"
        finally:
            os.close(fd)
            for rfd in received_fds:
                os.close(rfd)
            os.unlink(tmp_path)
    finally:
        sock1.close()
        sock2.close()


def test_send_recv_with_multiple_fds():
    """Send/receive data with multiple file descriptors."""
    sock1, sock2 = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    tmp_paths = []
    fds = []
    try:
        # Create multiple temp files
        for i in range(3):
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                tmp.write(f"content {i}".encode())
                tmp_paths.append(tmp.name)
            fds.append(os.open(tmp_paths[-1], os.O_RDONLY))

        data = b"message with multiple fds"
        send_with_fds(sock1, data, fds)
        received, received_fds = recv_with_fds(sock2)

        assert received == data
        assert len(received_fds) == 3

        # Verify each received fd
        for i, rfd in enumerate(received_fds):
            content = os.read(rfd, 100)
            assert content == f"content {i}".encode()
            os.close(rfd)
    finally:
        for fd in fds:
            os.close(fd)
        for path in tmp_paths:
            os.unlink(path)
        sock1.close()
        sock2.close()


def test_send_empty_fds_list():
    """Verify fallback to sendall when fds=[]."""
    sock1, sock2 = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        data = b"data without fds"
        # Empty list should use sendall path
        send_with_fds(sock1, data, [])
        received, fds = recv_with_fds(sock2)
        assert received == data
        assert fds == []
    finally:
        sock1.close()
        sock2.close()


# Server/Client socket tests


def test_create_server_socket(monkeypatch):
    """Verify socket creation and configuration."""
    with tempfile.TemporaryDirectory() as tmpdir:
        socket_path = os.path.join(tmpdir, "test.sock")
        monkeypatch.setattr("varlink_with_fd.socket.SOCKET_PATH", socket_path)

        server = create_server_socket()
        try:
            assert os.path.exists(socket_path)
            assert server.type == socket.SOCK_STREAM
            assert server.family == socket.AF_UNIX
            # Verify non-blocking
            assert server.getblocking() is False
        finally:
            server.close()
            if os.path.exists(socket_path):
                os.unlink(socket_path)


def test_create_client_socket_connects(monkeypatch):
    """Verify client connects to server."""
    with tempfile.TemporaryDirectory() as tmpdir:
        socket_path = os.path.join(tmpdir, "test.sock")
        monkeypatch.setattr("varlink_with_fd.socket.SOCKET_PATH", socket_path)

        server = create_server_socket()
        server.setblocking(True)
        try:
            client = create_client_socket()
            try:
                conn, _ = server.accept()
                try:
                    # Send data through the connection to verify it works
                    client.sendall(b"test")
                    assert conn.recv(4) == b"test"
                finally:
                    conn.close()
            finally:
                client.close()
        finally:
            server.close()
            if os.path.exists(socket_path):
                os.unlink(socket_path)


def test_server_socket_removes_existing(monkeypatch):
    """Verify old socket file is removed."""
    with tempfile.TemporaryDirectory() as tmpdir:
        socket_path = os.path.join(tmpdir, "test.sock")
        monkeypatch.setattr("varlink_with_fd.socket.SOCKET_PATH", socket_path)

        # Create a dummy file at the socket path
        with open(socket_path, "w") as f:
            f.write("dummy")
        assert os.path.exists(socket_path)

        server = create_server_socket()
        try:
            # Should still succeed - old file should be removed
            assert os.path.exists(socket_path)
        finally:
            server.close()
            if os.path.exists(socket_path):
                os.unlink(socket_path)
