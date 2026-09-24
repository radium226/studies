import hashlib
import hmac
import os
import socket
from dataclasses import dataclass
from typing import Any, Self

import httpx2
from loguru import logger

APP_ID = "xyz.rouages.unattended"
APP_NAME = "radium226 unattended"
APP_VERSION = "0.1.0"

DEFAULT_URL = "http://mafreebox.freebox.fr"


class FreeboxError(Exception):
    pass


@dataclass(frozen=True)
class Connection:
    state: str
    type: str
    media: str
    ipv4: str | None
    ipv6: str | None
    ipv4_port_range: tuple[int, int] | None

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> Self:
        port_range = payload.get("ipv4_port_range")
        return cls(
            state=payload["state"],
            type=payload.get("type", ""),
            media=payload.get("media", ""),
            ipv4=payload.get("ipv4") or None,
            ipv6=payload.get("ipv6") or None,
            ipv4_port_range=(port_range[0], port_range[1]) if port_range else None,
        )


class FreeboxClient:
    """
    Minimal client for the Freebox OS API (https://dev.freebox.fr/sdk/os/).

    The `app_token` comes from a one-off registration, see `credentials.py`.
    Both it and the box URL default to the `FREEBOX_APP_TOKEN` and `FREEBOX_URL`
    environment variables.
    """

    def __init__(self, app_token: str | None = None, url: str | None = None) -> None:
        self._app_token = app_token or os.environ["FREEBOX_APP_TOKEN"]
        self._url = (url or os.environ.get("FREEBOX_URL") or DEFAULT_URL).rstrip("/")
        self._http = httpx2.Client(timeout=10)
        self._api: str | None = None

    def _api_url(self, path: str) -> str:
        if self._api is None:
            version = self._http.get(f"{self._url}/api_version").json()
            self._api = f"{self._url}{version['api_base_url']}v{version['api_version'].split('.')[0]}"
            logger.debug("Freebox API at {} (version {})", self._api, version["api_version"])
        return f"{self._api}/{path.lstrip('/')}"

    def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self._http.request(method, self._api_url(path), **kwargs)
        payload = response.json()
        if not payload.get("success"):
            raise FreeboxError(f"{method} {path}: {payload.get('error_code')}: {payload.get('msg')}")
        return payload.get("result")

    def login(self) -> Self:
        challenge = self._call("GET", "login/")["challenge"]
        password = hmac.new(self._app_token.encode(), challenge.encode(), hashlib.sha1).hexdigest()
        session = self._call("POST", "login/session/", json={"app_id": APP_ID, "password": password})
        self._http.headers["X-Fbx-App-Auth"] = session["session_token"]
        logger.debug("Freebox session opened (permissions: {})", session.get("permissions"))
        return self

    def connection(self) -> Connection:
        connection = Connection.from_api(self._call("GET", "connection/"))
        logger.info(
            "Freebox connection: state={} ipv4={} ipv6={} ipv4_port_range={}",
            connection.state, connection.ipv4, connection.ipv6, connection.ipv4_port_range,
        )
        return connection

    # Registration, see `credentials.py`

    def authorize(self) -> tuple[str, int]:
        result = self._call(
            "POST",
            "login/authorize/",
            json={"app_id": APP_ID, "app_name": APP_NAME, "app_version": APP_VERSION, "device_name": socket.gethostname()},
        )
        return result["app_token"], result["track_id"]

    def authorization_status(self, track_id: int) -> str:
        return self._call("GET", f"login/authorize/{track_id}")["status"]
