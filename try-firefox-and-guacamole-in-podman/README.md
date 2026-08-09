# Try Firefox and Guacamole in Podman

A study: run Firefox headlessly in a rootless Podman pod and drive it from a phone
browser, with the phone as the *primary* target rather than an afterthought.

The twist that makes this more than a compose copy/paste: **the Java Guacamole webapp is
dropped entirely**. We keep `guacd` — the C proxy that actually speaks VNC — and write our
own Python service that bridges a browser WebSocket to guacd's wire protocol.

> Status: work in progress. See the phases below.

## Architecture

```
                    pod: firefox-guac   (shared localhost, only :8080 published)
  phone ──http──►  ┌──────────────────────────────────────────────┐
  :8080            │ tunnel  (uv + starlette)              :8080  │
                   │   GET  /         mobile page                 │
                   │   GET  /kiosk    diagnostic page             │
                   │   WS   /tunnel   ◄──► guacd                  │
                   │        │                                     │
                   │        ▼ 127.0.0.1:4822                      │
                   │ guacd   (guacamole/guacd:1.5.5)              │
                   │        │                                     │
                   │        ▼ 127.0.0.1:5900   (vnc)              │
                   │ firefox (Xvnc + openbox + firefox --kiosk)   │
                   └──────────────────────────────────────────────┘
```

## Usage

```sh
mise run build
mise run up
mise run url      # open this on your phone
mise run down
```
