from http.server import HTTPServer, BaseHTTPRequestHandler

from .find_ip import find_ip


class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        ip = find_ip()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(f"Hello, world! (ip={ip})".encode("utf-8"))


def serve_forever(host: str, port: int) -> None:
    server = HTTPServer((host, port), SimpleHTTPRequestHandler)
    server.serve_forever()