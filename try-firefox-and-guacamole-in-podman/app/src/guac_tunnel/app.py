"""The tunnel service: static pages plus the WebSocket that replaces Tomcat."""

from __future__ import annotations

import os

from loguru import logger
from starlette.applications import Starlette
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

from .bridge import GuacdConnection, bridge, tunnel_uuid_frame
from .config import STATIC_ROOT, Settings
from .handshake import Display, HandshakeError, perform_handshake
from .protocol import ProtocolError


def _page(name: str):
    async def handler(_request):
        return FileResponse(STATIC_ROOT / name)

    return handler


async def health(request) -> JSONResponse:
    settings: Settings = request.app.state.settings
    return JSONResponse(
        {
            "guacd": f"{settings.guacd_host}:{settings.guacd_port}",
            "remote": f"{settings.remote_protocol}://{settings.remote_host}:{settings.remote_port}",
            "display": [settings.default_width, settings.default_height, settings.default_dpi],
        }
    )


def _requested_display(websocket: WebSocket, settings: Settings) -> Display:
    """Read the geometry the browser asked for, falling back to the defaults."""

    def dimension(name: str, fallback: int) -> int:
        try:
            value = int(websocket.query_params.get(name, fallback))
        except ValueError:
            return fallback
        return max(1, min(value, settings.max_dimension))

    return Display(
        width=dimension("width", settings.default_width),
        height=dimension("height", settings.default_height),
        dpi=dimension("dpi", settings.default_dpi),
    )


async def tunnel(websocket: WebSocket) -> None:
    """Browser <-> guacd, for the lifetime of one session."""
    settings: Settings = websocket.app.state.settings
    display = _requested_display(websocket, settings)

    await websocket.accept(subprotocol="guacamole")

    try:
        connection = await GuacdConnection.open(settings.guacd_host, settings.guacd_port)
    except OSError as error:
        logger.error(
            "cannot reach guacd at {}:{}: {}", settings.guacd_host, settings.guacd_port, error
        )
        await websocket.close(code=1011, reason="guacd unreachable")
        return

    try:
        session = await perform_handshake(
            connection,
            protocol=settings.remote_protocol,
            parameters=settings.connection_parameters(),
            display=display,
        )
        logger.info(
            "connected {} {}x{} @{}dpi (guacd {}, id {})",
            settings.remote_protocol,
            display.width,
            display.height,
            display.dpi,
            ".".join(str(part) for part in session.version),
            session.connection_id,
        )

        # Must precede any session data, or the browser never opens the tunnel.
        await websocket.send_text(tunnel_uuid_frame())

        # guacd usually starts drawing in the same read that carried `ready`.
        if buffered := connection.drain_buffered():
            await websocket.send_text(buffered)

        # A successful handshake does not mean the remote desktop is up: guacd
        # answers `ready` before it has reached weston. A failure there surfaces
        # as an `error` instruction *inside* the session stream. The browser
        # reports that; nothing to catch here.
        await bridge(connection, websocket)

    except (HandshakeError, ProtocolError) as error:
        logger.error("handshake failed: {}", error)
        await websocket.close(code=1011, reason=str(error)[:120])
    except (WebSocketDisconnect, ConnectionResetError, ConnectionError):
        logger.info("session ended")
    finally:
        await connection.close()


def create_app(settings: Settings | None = None) -> Starlette:
    app = Starlette(
        routes=[
            Route("/", _page("index.html")),
            Route("/diagnostics", _page("diagnostics.html")),
            Route("/health", health),
            WebSocketRoute("/tunnel", tunnel),
            Mount("/static", StaticFiles(directory=STATIC_ROOT), name="static"),
        ]
    )
    app.state.settings = settings or Settings.from_env()
    return app


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run(
        "guac_tunnel.app:app",
        # Binding every interface is the point: the phone connects over the LAN.
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8080")),
        log_level=os.environ.get("LOG_LEVEL", "info"),
    )


if __name__ == "__main__":
    main()
