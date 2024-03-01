from click import command, group, pass_context, Context, option, argument
from os import environ
from pathlib import Path
import yaml
import json
from types import SimpleNamespace
from subprocess import run

from .pia import PIA
from .models import (
    User,
    Password,
    RegionID,
    PayloadAndSignature,
    Auth,
    AppName
)


DEFAULT_NUMBER_OF_PORTS_TO_FORWARD = 0
DEFAULT_CALLBACK = "true"

@group()
@option("--app", "app_name", type=AppName, required=False, default="pia")
@option("--state-folder", "state_folder_path", type=Path, default=Path("/var/run/vpn-passthrough"))
@option("--config-file", "config_file_path", type=Path, default=Path("/etc/vpn-passthrough/config.yaml"))
@option("--pia-user", "pia_user", type=User, required=False)
@option("--pia-password", "pia_password", type=Password, required=False)
@option("--pia-auth-file", "pia_auth_file_path", type=Path, default=Path("/etc/vpn-passthrough/pia.txt"))
@pass_context
def app(
    context: Context, 
    config_file_path: Path, 
    state_folder_path: Path, 
    app_name: str,
    pia_user: User | None,
    pia_password: Password | None,
    pia_auth_file_path: Path | None,
):
    config = yaml.safe_load(config_file_path.read_text())
    
    args_pia_auth = Auth(pia_user, pia_password) if pia_user and pia_password else None
    file_pia_auth = PIA.read_auth_file(pia_auth_file_path) if pia_auth_file_path else None
    env_pia_auth = Auth(User(environ["PIA_USER"]), Password(environ["PIA_PASSWORD"])) if "PIA_USER" in environ and "PIA_PASSWORD" in environ else None
    
    pia_auth = args_pia_auth or file_pia_auth or env_pia_auth
    if not pia_auth:
        raise Exception("No PIA auth found!")

    context.obj = SimpleNamespace(
        state_folder_path=state_folder_path,
        pia=PIA(auth=pia_auth),
        config=config,
        app_name=app_name,
    )


@app.group()
@pass_context
def pia(context: Context):
    pass


@pia.command()
@option("--region", "-r", "region_id", type=RegionID, required=False)
@pass_context
def connect(context: Context, region_id: RegionID | None):
    config = context.obj.config
    config_region_id = RegionID(config["region_id"]) if "region_id" in config else None
    env_region_id = RegionID(environ["VPN_PASSTHROUGH_REGION_ID"]) if "VPN_PASSTHROUGH_REGION_ID" in environ else None

    region_id = region_id or config_region_id or env_region_id
    if not region_id:
        raise Exception("No region found!")

    pia = context.obj.pia
    pia.connect(region_id=region_id)


@pia.command()
@argument("payload_and_signature_file_path", type=Path, required=True)
@pass_context
def bind_port(context: Context, payload_and_signature_file_path: Path):
    pia = context.obj.pia
    payload_and_signature = PIA.read_payload_and_signature(payload_and_signature_file_path)
    pia.bind_port(payload_and_signature)


@pia.command()
@argument("payload_and_signature_file_path", type=Path, required=True)
@pass_context
def request_port(context: Context, payload_and_signature_file_path: Path):
    pia = context.obj.pia
    port, payload_and_signature = pia.request_port()
    print(port)
    PIA.write_payload_and_signature(payload_and_signature, payload_and_signature_file_path)


@app.group()
def veth():
    pass

@veth.command()
@pass_context
def create(context: Context):
    app_name = context.obj.app_name

    netns = app_name
    
    veth_iface = context.obj.config["dev"]["veth"]["iface"]
    veth_addr = context.obj.config["dev"]["veth"]["addr"]
    vpeer_iface = context.obj.config["dev"]["vpeer"]["iface"]
    vpeer_addr = context.obj.config["dev"]["vpeer"]["addr"]

    run(["ip", "link", "add", veth_iface, "type", "veth", "peer", "name", vpeer_iface, "netns", netns], check=True)
    run(["ip", "addr", "add", f"{veth_addr}/24", "dev", veth_iface], check=True)
    run(["ip", "link", "set", veth_iface, "up"], check=True)

    run(["ip", "netns", "exec", netns, "ip", "addr", "add", f"{vpeer_addr}/24", "dev", vpeer_iface], check=True)
    run(["ip", "netns", "exec", netns, "ip", "link", "set", vpeer_iface, "up"], check=True)

    run(["ip", "netns", "exec", netns, "ip", "link", "set", "lo", "up"], check=True)

    run(["ip", "netns", "exec", netns, "ip", "route", "add", "default", "via", veth_addr], check=True)


@veth.command()
@pass_context
def destroy(context: Context):
    veth_iface = context.obj.config["dev"]["veth"]["iface"]
    run(["ip", "link", "delete", veth_iface], check=True)


@app.group
def configuration():
    pass


@configuration.command()
@option("--callback", "callback", type=str, required=False)
@option("--number-of-ports-to-forward", "number_of_ports_to_forward", type=int, required=False)
@pass_context
def setup(context: Context, callback: str | None, number_of_ports_to_forward: int | None):
    pia = context.obj.pia
    config = context.obj.config

    callback = callback or config["callback"] or DEFAULT_CALLBACK

    print("callback: ", callback)

    number_of_ports_to_forward = number_of_ports_to_forward or config["port_forwards"]["number_of"] or DEFAULT_NUMBER_OF_PORTS_TO_FORWARD
    
    ports = []
    for _ in range(number_of_ports_to_forward):
        port, payload_and_signature = pia.request_port()
        pia.bind_port(payload_and_signature)
        context.obj.state_folder_path.mkdir(parents=True, exist_ok=True)
        text = json.dumps(dict(
            payload=payload_and_signature.payload,
            signature=payload_and_signature.signature,
        ))

        payload_and_signature_file_path = context.obj.state_folder_path / f"{port}.json"
        payload_and_signature_file_path.write_text(text)

        specifier = run(["systemd-escape", "-p", str(payload_and_signature_file_path)], capture_output=True, text=True, check=True).stdout.strip()
        command = [
        "systemd-run", 
            "--timer-property", "PartOf=vpn-passthrough-configuration.service",
            "--on-calendar=*-*-* *:*:00,30",
            "--unit", "vpn-passthrough-pia-bind-port@{specifier}".format(specifier=str(specifier))
        ]
        run(command, check=True)
        ports.append(port)

    if callback:
        run(["bash", "-c", callback, "callback"] + [str(port) for port in ports], check=True)


@configuration.command()
def teardown():
    pass


@app.group()
def forwarding():
    pass


@forwarding.command()
@pass_context
def start(context: Context):
    app_name = context.obj.app_name
    
    veth_iface = context.obj.config["dev"]["veth"]["iface"]
    veth_addr = context.obj.config["dev"]["veth"]["addr"]
    vpeer_iface = context.obj.config["dev"]["vpeer"]["iface"]
    vpeer_addr = context.obj.config["dev"]["vpeer"]["addr"]

    command=[
        "nft",
        "-f", "/etc/vpn-passthrough/internet.nft",
        "-D", f"app_name={app_name}",
        "-D", f"veth_iface={veth_iface}",
        "-D", f"veth_addr={veth_addr}",
        "-D", f"vpeer_iface={vpeer_iface}",
        "-D", f"vpeer_addr={vpeer_addr}",
    ]
    process = run(command, check=False)
    print(process.stdout)
    print(process.stderr)
    if process.returncode != 0:
        raise Exception("Failed to start forwarding")


@forwarding.command()
@pass_context
def stop(context: Context):
    app_name = context.obj.app_name
    command = ["nft", "delete", "table", "inet", str(app_name)]
    run(command, check=True)


@app.group()
def netns():
    pass


@netns.command()
@pass_context
def create(context: Context):
    app_name = context.obj.app_name
    command = ["ip", "netns", "add", str(app_name)]
    run(command, check=True)


@netns.command()
@pass_context
def destroy(context: Context):
    app_name = context.obj.app_name
    command = ["ip", "netns", "delete", str(app_name)]
    run(command, check=True)