from click import group, option, pass_context, Context
from os import environ
from pathlib import Path
import json
import sys
from types import SimpleNamespace

from .serve_forever import serve_forever
from .find_ip import find_ip

DEFAULT_PORT = 8765
DEFAULT_HOST = "0.0.0.0"


@group()
@option("--instance", "instance", type=str, required=False, default="default")
@pass_context
def app(context: Context, instance: str):
    context.obj = SimpleNamespace(
        instance=instance,
    )

@app.command()
def show_ip():
   ip = find_ip()
   print(f"ip: {ip}")


@app.command()
@option("--host", "host", type=str, required=False)
@option("--port", "port", type=int, required=False)
@option("--config-folder", "config_folder_path", type=Path, default=Path("/etc/dummy-app"))
@pass_context
def serve(context: Context, host: str | None, port: int | None, config_folder_path: Path):
    config_file_path = config_folder_path / f"{context.obj.instance}.json"
    config = json.loads(config_file_path.read_text()) if config_file_path.exists() else {}
    host = host or config.get("host", None) or environ.get("DUMMY_APP_HOST", None) or DEFAULT_HOST
    port = port or config.get("port", None) or ( int(port) if ( port := environ.get("DUMMY_APP_PORT", None) ) else None ) or DEFAULT_PORT
    print(f"instance: {isinstance}, host: {host}, port: {port}")
    sys.stdout.flush()
    serve_forever(
        host=host,
        port=port,
    )

