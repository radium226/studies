# guac-tunnel

Bridges a browser WebSocket to `guacd`'s wire protocol, replacing the Java Guacamole
webapp. Also serves the mobile-first client page.

- `protocol.py` — Guacamole instruction encode/decode. Lengths are in **Unicode
  characters, not bytes**.
- `handshake.py` — `select` → `args` → `size`/`audio`/`video`/`image` → `connect` → `ready`.
- `bridge.py` — the two asyncio pumps.
- `app.py` — Starlette routes.
