import os
import socket
import tempfile

from varlink_with_fd.protocol import Request, Response, read_message
from varlink_with_fd.socket import recv_with_fds, send_with_fds


def test_client_server_request_response():
    """Full request/response cycle over sockets."""
    sock1, sock2 = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        # Client sends request
        request = Request(
            method="com.example.Echo",
            parameters={"message": "hello"},
        )
        send_with_fds(sock1, request.encode(), [])

        # Server receives and processes
        data, fds = recv_with_fds(sock2)
        message, _ = read_message(data)
        received_request = Request.decode(message)

        assert received_request.method == "com.example.Echo"
        assert received_request.parameters == {"message": "hello"}

        # Server sends response
        response = Response(
            parameters={"echo": received_request.parameters["message"]}
        )
        send_with_fds(sock2, response.encode(), [])

        # Client receives response
        data, fds = recv_with_fds(sock1)
        message, _ = read_message(data)
        received_response = Response.decode(message)

        assert received_response.parameters == {"echo": "hello"}
        assert received_response.error is None
    finally:
        sock1.close()
        sock2.close()


def test_client_server_with_fd_passing():
    """Request/response with file descriptor."""
    sock1, sock2 = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    tmp_path = None
    try:
        # Create a file to pass
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as tmp:
            tmp.write("file content to share")
            tmp_path = tmp.name

        fd = os.open(tmp_path, os.O_RDONLY)
        try:
            # Client sends request with fd
            request = Request(
                method="com.example.ReadFile",
                parameters={"filename": "shared.txt"},
            )
            send_with_fds(sock1, request.encode(), [fd])

            # Server receives request with fd
            data, received_fds = recv_with_fds(sock2)
            message, _ = read_message(data)
            received_request = Request.decode(message)

            assert received_request.method == "com.example.ReadFile"
            assert len(received_fds) == 1

            # Server reads from the received fd
            content = os.read(received_fds[0], 1024)
            assert content == b"file content to share"

            # Server sends response
            response = Response(parameters={"bytes_read": len(content)})
            send_with_fds(sock2, response.encode(), [])

            # Cleanup received fds
            for rfd in received_fds:
                os.close(rfd)

            # Client receives response
            data, fds = recv_with_fds(sock1)
            message, _ = read_message(data)
            received_response = Response.decode(message)

            assert received_response.parameters == {"bytes_read": 21}
        finally:
            os.close(fd)
    finally:
        sock1.close()
        sock2.close()
        if tmp_path:
            os.unlink(tmp_path)
