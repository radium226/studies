from click import group, option
from os import environ
from pathlib import Path
import json
import sys

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
@option("--config-file", "config_file_path", type=Path, default=Path("/etc/dummy-app/config.json"))
def serve(host: str | None, port: int | None, config_file_path: Path):
    config = json.loads(config_file_path.read_text())
    
    host = host or config.get("host", None) or environ.get("DUMMY_APP_HOST", None) or DEFAULT_HOST
    port = port or config.get("port", None) or ( int(port) if ( port := environ.get("DUMMY_APP_PORT", None) ) else None ) or DEFAULT_PORT
    print(f"host: {host}, port: {port}")
    sys.stdout.flush()
    serve_forever(
        host=host,
        port=port,
    )

