from click import group, option

from .serve_forever import serve_forever
from .find_ip import find_ip

DEFAULT_PORT = 8765
DEFAULT_HOST = "localhost"


@group()
def app():
    pass

@app.command()
def show_ip():
   ip = find_ip()
   print(f"ip: {ip}")


@app.command()
@option("--host", "host", type=str, required=False)
@option("--port", "port", type=int, required=False)
def serve(host: str | None, port: int | None):
    host = host or DEFAULT_HOST
    port = port or DEFAULT_PORT
    serve_forever(
        host=host or DEFAULT_HOST,
        port=port or DEFAULT_PORT,
    )

