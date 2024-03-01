import requests
from subprocess import run
from os import execvpe, environ
import json
from base64 import b64decode
from pathlib import Path
import sys

from .models import (
    User,
    Password,
    RegionID,
    Auth,
    PayloadAndSignature,
    Payload,
    Signature
)


class PIA():

    AUTH_TOKEN_URL = "https://www.privateinternetaccess.com/api/client/v2/token"
    DIP_URL = "https://www.privateinternetaccess.com/api/client/v2/dedicated_ip"
    SERVERS_URL = "https://serverlist.piaservers.net/vpninfo/servers/v6"

    auth: Auth

    def __init__(self, auth: Auth):
        self.auth = auth


    @classmethod
    def read_auth_file(cls, file_path: Path) -> Auth:
        text = file_path.read_text()
        [user, password] = text.splitlines()
        auth = Auth(
            user=User(user),
            password=Password(password),
        )
        return auth
    

    @classmethod
    def read_payload_and_signature_file(cls, file_path: Path) -> PayloadAndSignature:
        obj = json.loads(file_path.read_text())
        return PayloadAndSignature(
            payload=Payload(obj["payload"]),
            signature=Signature(obj["signature"]),
        )
    

    @classmethod
    def write_payload_and_signature_file(cls, payload_and_signature: PayloadAndSignature, file_path: Path) -> PayloadAndSignature:
        file_path.write_text(json.dumps({
            "payload": str(payload_and_signature.payload),
            "signature": str(payload_and_signature.signature),
        }))


    def connect(self, region_id: RegionID, openvpn_dev: str = "tun0", netns: str | None = None):
        response = requests.get(self.SERVERS_URL, headers={"Accept": "application/json"})
        [text, *_] = response.text.splitlines()
        obj = json.loads(text)

        region = next((region for region in obj["regions"] if region["id"] == region_id), None)
        if not region:
            raise Exception(f"Unknown region! (region_id={region_id})")
        
        openvpn_remote = region["servers"]["ovpnudp"][0]["ip"]
        # FIXME: Find a way to get this port somewhere
        openvpn_port = 1198

        ip_command = ["ip", "netns", "exec", netns] if netns else []

        openvpn_command = [
            "openvpn",
            "--client",
            "--dev", openvpn_dev,
            "--proto", "udp",
            "--resolv-retry", "infinite",
            "--nobind", 
            "--persist-key",
            "--persist-tun", 
            "--cipher", "AES-256-CBC",
            "--data-ciphers-fallback", "AES-256-CBC",
            "--auth", "sha1",
            "--tls-client", 
            "--remote-cert-tls", "server",
            "--auth-user-pass", 
            "--compress",
            "--reneg-sec", "0",
            "--crl-verify", "/etc/vpn-passthrough/ca.rsa.2048.crl",
            "--ca", "/etc/vpn-passthrough/ca.rsa.2048.crt",
            "--disable-occ", 
            "--errors-to-stderr", 
            "--pull-filter", "ignore", "route-ipv6",
            "--pull-filter", "ignore", "ifconfig-ipv6",
            "--remote", openvpn_remote, 
            "--port", str(openvpn_port),
            "--auth-user-pass", "/etc/vpn-passthrough/pia.txt",
            "--auth-nocache",
            "--script-security", "2",
            "--up", "/etc/vpn-passthrough/openvpn-script",
            "--down", "/etc/vpn-passthrough/openvpn-script",
        ]

        command = ip_command + openvpn_command

        print(command)
        sys.stdout.flush()

        execvpe(
            command[0], 
            command,
            env=environ,
        )

    
    def generate_auth_token(self) -> str:
        command = [
            "curl", 
            "-s",
            "--location",
            "--request", "POST",
            "--form", "username={user}".format(user=str(self.auth.user)),
            "--form", "password={password}".format(password=str(self.auth.password)),
            self.AUTH_TOKEN_URL,
        ]

        process = run(command, text=True, check=True, capture_output=True)
        stdout = process.stdout
        obj = json.loads(stdout)
        auth_token = obj["token"]
        return auth_token
    
    def lookup_gateway(self):
        # FIXME: Find a better way to get the gateway address
        process = run(["ip", "route", "show", "0.0.0.0/1"], capture_output=True, text=True, check=True)
        stdout = process.stdout
        gateway = stdout.split(" ")[2]
        return gateway


    # def check_dip(self):
    #     auth_token = self.generate_auth_token()
    #     dip_token = self.dip_token
    #     print(dip_token)
    #     command = [
    #         "curl", 
    #         "--location", 
    #         "--request", "POST",
    #         "--header", "Content-Type: application/json",
    #         "--header", f"Authorization: Token {auth_token}",
    #         "--data-raw", f'{{"tokens":["{self.dip_token}"]}}',
    #         DIP_URL,
    #     ]
    #     stdout = run(command, capture_output=True, text=True, check=True).stdout
    #     obj = json.loads(stdout)
    #     return obj


    def generate_payload_and_signature(self) -> PayloadAndSignature:
        gateway = self.lookup_gateway()
        auth_token = self.generate_auth_token()
        print(auth_token)
        # FIXME: Use cert
        command = [
            "curl", "-kG",
            "--data-urlencode", f"token={auth_token}",
            f"https://{gateway}:19999/getSignature",
        ]
        process = run(command, capture_output=True, text=True, check=True)
        stdout = process.stdout
        obj = json.loads(stdout)
        print(obj)
        payload = obj["payload"]
        signature = obj["signature"]
        payload_and_signature = PayloadAndSignature(
            payload=payload,
            signature=signature,
        )
        return payload_and_signature
    
    def request_port(self) -> tuple[int, PayloadAndSignature]:
        payload_and_signature = self.generate_payload_and_signature()
        payload = payload_and_signature.payload
        obj = json.loads(b64decode(payload).decode("utf-8"))
        port = int(obj["port"])
        return port, payload_and_signature
    

    def bind_port(self, payload_and_signature: PayloadAndSignature):
        payload = payload_and_signature.payload
        signature = payload_and_signature.signature
        gateway = self.lookup_gateway()
        command = [
                "curl",
                "-sGk", 
                "--data-urlencode", f"payload={payload}",
                "--data-urlencode", f"signature={signature}",
                f"https://{gateway}:19999/bindPort",
            ]
        print(" ".join(command))
        stdout = run(command, capture_output=True, text=True, check=True).stdout
        obj = json.loads(stdout)
        status = obj["status"]
        message = obj["message"]
        if status != "OK":
            raise Exception(
                "Unable to bind port! (status={status}, message={message})".format(
                    status=status, 
                    message=message
                )
            )