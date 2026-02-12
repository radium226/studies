import signal

from varlink_with_fd.protocol import Request, Response, read_message


def test_signal_enum_serialization():
    """Signal enums should serialize as integers, not enum names."""
    request = Request(method="Signal", parameters={"signal": int(signal.SIGINT)})
    encoded = request.encode()
    assert b'"signal": 2' in encoded or b'"signal":2' in encoded


# Request class tests


def test_request_encode_basic():
    """Encode request with method only."""
    request = Request(method="com.example.Method")
    encoded = request.encode()
    assert encoded.endswith(b"\0")
    assert b'"method"' in encoded
    assert b"com.example.Method" in encoded


def test_request_encode_with_parameters():
    """Encode request with parameters."""
    request = Request(
        method="com.example.Method",
        parameters={"key": "value", "count": 42},
    )
    encoded = request.encode()
    assert b'"key"' in encoded
    assert b'"value"' in encoded
    assert b"42" in encoded


def test_request_decode_basic():
    """Decode valid request bytes."""
    data = b'{"method": "com.example.Method", "parameters": {}}'
    request = Request.decode(data)
    assert request.method == "com.example.Method"
    assert request.parameters == {}


def test_request_decode_with_parameters():
    """Decode request with complex parameters."""
    data = b'{"method": "test", "parameters": {"nested": {"a": 1}, "list": [1, 2, 3]}}'
    request = Request.decode(data)
    assert request.method == "test"
    assert request.parameters == {"nested": {"a": 1}, "list": [1, 2, 3]}


def test_request_roundtrip():
    """Encode then decode, verify equality."""
    original = Request(
        method="com.example.Test",
        parameters={"foo": "bar", "num": 123},
    )
    encoded = original.encode()
    # Remove null terminator for decode
    decoded = Request.decode(encoded[:-1])
    assert decoded.method == original.method
    assert decoded.parameters == original.parameters


# Response class tests


def test_response_encode_with_parameters():
    """Encode response with parameters."""
    response = Response(parameters={"result": "success", "data": [1, 2, 3]})
    encoded = response.encode()
    assert encoded.endswith(b"\0")
    assert b'"result"' in encoded
    assert b'"success"' in encoded


def test_response_encode_with_error():
    """Encode error response."""
    response = Response(error="com.example.Error")
    encoded = response.encode()
    assert b'"error"' in encoded
    assert b"com.example.Error" in encoded


def test_response_encode_empty():
    """Encode response with no parameters or error."""
    response = Response()
    encoded = response.encode()
    assert encoded == b"{}\0"


def test_response_decode_with_parameters():
    """Decode response with parameters."""
    data = b'{"parameters": {"key": "value"}}'
    response = Response.decode(data)
    assert response.parameters == {"key": "value"}
    assert response.error is None


def test_response_decode_with_error():
    """Decode error response."""
    data = b'{"error": "com.example.NotFound"}'
    response = Response.decode(data)
    assert response.error == "com.example.NotFound"
    assert response.parameters is None


def test_response_roundtrip():
    """Encode then decode, verify equality."""
    original = Response(parameters={"status": "ok"})
    encoded = original.encode()
    decoded = Response.decode(encoded[:-1])
    assert decoded.parameters == original.parameters
    assert decoded.error == original.error


def test_response_excludes_none_fields():
    """Verify exclude_none behavior."""
    response = Response(parameters={"value": 1}, error=None)
    encoded = response.encode()
    # "error" should not appear in the output
    assert b'"error"' not in encoded
    assert b'"parameters"' in encoded


# read_message function tests


def test_read_message_complete():
    """Extract complete message from buffer."""
    buffer = b'{"method": "test"}\0remaining'
    message, remaining = read_message(buffer)
    assert message == b'{"method": "test"}'
    assert remaining == b"remaining"


def test_read_message_incomplete():
    """Return None for incomplete message."""
    buffer = b'{"method": "test"'
    message, remaining = read_message(buffer)
    assert message is None
    assert remaining == buffer


def test_read_message_multiple():
    """Handle multiple messages in buffer."""
    buffer = b'{"method": "first"}\0{"method": "second"}\0'
    # First extraction
    message1, remaining = read_message(buffer)
    assert message1 == b'{"method": "first"}'
    # Second extraction
    message2, remaining = read_message(remaining)
    assert message2 == b'{"method": "second"}'
    assert remaining == b""


def test_read_message_empty_buffer():
    """Handle empty buffer."""
    buffer = b""
    message, remaining = read_message(buffer)
    assert message is None
    assert remaining == b""
