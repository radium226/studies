# Try Firefox and Guacamole in Podman

Run Firefox headlessly in a rootless Podman pod and drive it from a phone browser, with
the phone as the *primary* target rather than an afterthought.

The twist that makes this a study rather than a compose copy/paste: **the Java Guacamole
webapp is dropped entirely.** What stays is `guacd`, the C proxy that actually speaks VNC.
What replaces Tomcat is ~250 lines of Python that bridge a browser WebSocket to guacd's
wire protocol. Removing the webapp is also what makes genuinely zero-auth, single-URL
access possible: there is no login form to skip, because there is no auth subsystem.

| | |
|---|---|
| ![portrait](docs/client-portrait.png) | ![landscape](docs/client-landscape.png) |
| A 450×1000 CSS px phone viewport showing the remote 1080×2400 session at 38%. | The same session after rotating: letterboxed at 15%, still connected. |

Both screenshots are a real Firefox, phone-shaped, rendering the client page — which is
showing a *second* Firefox running inside the pod. That inner Firefox is an ordinary
browser: real tabs, a real URL bar, a real menu. You can navigate it from the phone.

## Running it

```sh
mise run build     # three images, and vendor guacamole-common-js
mise run up        # one pod, three containers
mise run url       # prints the LAN address to open on your phone
mise run down
```

Everything is driven by `mise`; there is no Makefile, no compose file and no kube YAML.

## Architecture

```
                    pod: firefox-guac   (shared localhost, only :8080 published)
  phone ──http──►  ┌──────────────────────────────────────────────┐
  :8080            │ tunnel  (uv + starlette)              :8080  │
                   │   GET  /             the mobile client       │
                   │   GET  /diagnostics  diagnostic page         │
                   │   WS   /tunnel       ◄──► guacd              │
                   │        │                                     │
                   │        ▼ 127.0.0.1:4822                      │
                   │ guacd   (guacamole/guacd:1.5.5)              │
                   │        │                                     │
                   │        ▼ 127.0.0.1:5900   (vnc)              │
                   │ firefox (Xvnc + openbox + firefox)           │
                   └──────────────────────────────────────────────┘
```

Containers in a pod share a network namespace, so all three talk over loopback and only
port 8080 is published. That is the only reason Xvnc can run with `-SecurityTypes None`:
5900 is not reachable from anywhere but the pod.

## The protocol

Guacamole instructions are a comma-separated list of length-prefixed elements ending in a
semicolon; the first element is the opcode.

```
6.select,3.vnc;
^      ^ ^   ^
|      | |   +-- terminator
|      | +------ value
|      +-------- separator
+--------------- length of "select"
```

`app/src/guac_tunnel/protocol.py` encodes and decodes these;
`app/src/guac_tunnel/handshake.py` drives connection setup. A real exchange, captured
against guacd 1.5.5 by `mise run up` and a narrating client:

```
client ──► 6.select,3.vnc;
guacd  ──► 4.args,13.VERSION_1_5_0,8.hostname,4.port,9.read-only,…   (796 chars, 44 parameters)
client ──► 4.size,4.1080,4.2400,3.420;
client ──► 5.audio,8.audio/L8,9.audio/L16;
client ──► 5.video;
client ──► 5.image,10.image/jpeg,9.image/png,10.image/webp;
client ──► 7.connect,13.VERSION_1_5_0,9.127.0.0.1,4.5900,0.,0.,0.,0.,0.,2.24,6.remote,1.3,…
guacd  ──► 5.ready,37.$3f910fa8-1b60-4d5f-ab9e-4845257a32aa;

           …and the session stream begins:

guacd  ──► 4.size,2.-1,1.5,1.5;
guacd  ──► 3.img,1.1,2.12,2.-1,9.image/png,1.0,1.0;
guacd  ──► 4.blob,1.1,152.iVBORw0KGgoAAAANSUhEUgAAAAUAAAAF…
guacd  ──► 3.end,1.1;
guacd  ──► 6.cursor,1.2,1.2,2.-1,1.0,1.0,1.5,1.5;
guacd  ──► 3.set,1.0,11.multi-touch,1.0;
```

Three things in there are worth pausing on.

**`connect` is positional.** Its values line up with the parameter names guacd sent in
`args` — all 44 of them, in that order, with an empty string for each of the 39 we do not
set. Sending only the ones we care about connects to garbage rather than failing.

**Slot 0 is a version, not a parameter.** From 1.5.0 guacd puts `VERSION_1_5_0` first in
`args`, and expects the negotiated version echoed back in that slot. Treating it as a
parameter name shifts every subsequent value by one.

**`ready` does not mean connected.** guacd answers `ready` before it has reached the VNC
server, so a dead Xvnc surfaces later as an `error` instruction *inside* the session
stream rather than as a handshake failure.

## Three things that bit

Each of these failed silently — no exception, no error log, just a black screen.

### The browser's tunnel keeps no buffer between messages

`guacamole-common-js` parses each WebSocket message on its own and fails the connection
with `Incomplete instruction.` on any message that ends mid-instruction. The bridge was
forwarding raw TCP chunks straight through, and TCP splits wherever it likes. Sessions
connected, handshook, and died ~285 ms later with nothing rendered.

Forwarding is now re-split on instruction boundaries (`InstructionParser.feed_complete`),
holding any partial tail back until the rest arrives. `test_no_message_ever_ends_mid_instruction`
pushes a stream in three-character slices and asserts every message the browser receives
parses standalone.

### `Guacamole.Client` owns `tunnel.oninstruction`

It installs its own handler in its constructor, and that handler is what draws every
frame. Instrumenting the instruction stream by assigning to `tunnel.oninstruction`
disables rendering entirely. Chain onto the existing handler instead.

Relatedly, `WebSocketTunnel` builds its socket URL as `url + "?" + data`, so the tunnel URL
must carry no query string of its own or you get two `?` and lose the parameters.

### Element lengths count characters, not bytes

The protocol is defined over a UTF-8 stream but lengths are in Unicode characters, so
`2.é;` is valid and three bytes long. Counting bytes desynchronises the parser the first
time anything non-ASCII crosses the wire — a clipboard paste, a window title, an accented
keystroke.

## What "mobile first" meant concretely

Getting a desktop Firefox to behave like a phone browser took three specific values, each
verified by reading them back out of a running instance (`mise run up`, then open `/diagnostics`):

**Firefox will not size its window below 450 CSS px**, maximised or not. Ask for less and
the window comes out *wider* than the screen, clipping the right edge of every page. So
the framebuffer is sized in physical pixels like a real phone panel — 1080×2400 — and
divided by `layout.css.devPixelsPerPx = 2.4`, which lands exactly on that floor: a
450×1000 CSS viewport, rendered at 2.4×.

**The pointer capability bitfield is `Coarse=1, Fine=2, Hover=4`.** Setting
`ui.primaryPointerCapabilities` to 2 for "coarse" quietly reports a mouse, and every site
that branches on `(pointer: coarse)` serves its desktop layout.

**Enabling touch events does not expose `ontouchstart`.** `dom.w3c_touch_events.enabled`
turns the events on, but the property that feature detection actually reads stays
undefined until `dom.w3c_touch_events.legacy_apis.enabled` is set too.

Firefox runs as an ordinary browser here — tabs, URL bar, menu, all navigable from the
phone — so it has to be *told* to fill the screen, and there is no command-line flag for
that. Two pieces: the window geometry lives in the profile, so `session.sh` seeds
`xulstore.json` with `sizemode: maximized` before first start; and openbox is configured
with `<decor>no</decor>`, because its title bar otherwise costs 61 px of a phone-sized
screen to duplicate a title Firefox already shows.

The result, read off the diagnostic page inside the container:

```
viewport      450×915 css px   (1000 minus Firefox's own chrome)
dpr           2.4
pointer       coarse
hover         no
touch events  yes
user agent    Mozilla/5.0 (Android 14; Mobile; rv:128.0) Gecko/128.0 Firefox/128.0
```

On the client side: `100dvh` rather than `100vh` (on a phone `vh` lies about the URL bar),
`viewport-fit=cover` with `env(safe-area-inset-*)` on the controls, `touch-action: none`
and `overscroll-behavior: none` so a drag reaches the remote session instead of
rubber-banding the page, and 44 px tap targets.

## Touch, on something that only understands a mouse

guacd speaks VNC and VNC speaks mouse, so every gesture has to be translated.
`static/input.js` maps them:

| gesture | sent to the remote |
|---|---|
| tap | move, then left click at that point |
| one-finger drag | **scroll wheel**, not a mouse drag |
| long press (500 ms) | right click |
| two-finger tap | right click |

The drag mapping is the one that matters. `Guacamole.Mouse.Touchscreen` maps a one-finger
drag to a mouse drag, which is correct for a desktop but wrong here: to the remote a mouse
drag means *select text*, so trying to scroll a page smears a selection across it instead.
Dragging emits wheel notches instead, one per 40 px of travel.

Touch and mouse deliberately share one coordinate path —
`Guacamole.Position.fromClientPosition` plus `sendMouseState(state, true)`, which makes the
client divide by the display scale itself — so a tap and a click cannot disagree about
where they landed.

The keyboard needs its own workaround, and this is where the sharpest trap in the whole
study is. Phone keyboards do not report keys: they send `keyCode 229` and commit text, so
keystrokes have to be reconstructed from `beforeinput` on a hidden capture field.

**`Guacamole.Keyboard` cannot be used alongside that.** It calls `preventDefault()` on
every keydown it sees — correct when it owns the keyboard, fatal here, because a cancelled
keydown never inserts text, so the field never emits `beforeinput`. Attaching both produces
a keyboard that does nothing whatsoever, with no error anywhere. It is gone; text comes
from `beforeinput` and keys that produce no text (arrows, Tab, Escape, shortcuts) come from
`keydown` on the same field.

The field is also seeded with filler, because backspace on an empty field has nothing to
delete and so fires no event at all — backspace silently does nothing.

Expand the debug strip to see `last input`: it reports the gesture and the remote
coordinates actually sent.

## What it cost to drop RDP

Guacamole's `resize-method=display-update` is **RDP-only**. Over VNC there is no
client-driven dynamic resize, so rotating the phone cannot reflow the remote session.

Instead the display is scaled to fit: rotation rescales and letterboxes, staying connected
throughout, and the debug strip shows remote geometry and scale factor side by side so the
difference is visible rather than mysterious. In the screenshots above the same 1080×2400
session renders at 38% in portrait and 15% in landscape.

For a real geometry change there is an escape hatch:

```sh
mise run resize 2400 1080     # then reload the page
```

That recreates the Firefox container. Doing it in place with `xrandr --fb` *does* resize
the framebuffer and Firefox *does* reflow, but it leaves the RandR output marked
disconnected, and Xvnc has no modeline generator aboard to do it properly — `cvt` ships
with `xserver-xorg-core`, which is precisely what using Xvnc avoids.

Choosing Xvnc over xrdp is what made the Firefox container short: Xvnc is both the X
server and the VNC server in one process, so there is no session manager, no PAM, no
`Xwrapper.config`, no RSA keygen, and nothing that needs to be root.

## Limits

- **No auth, anywhere.** Anyone on the LAN who opens the URL gets a live browser. That is
  a deliberate choice for an experiment, not an oversight.
- **HTTP only**, so no secure context: `navigator.clipboard` is unavailable and the page
  cannot be installed as a PWA. Touch, fullscreen and rotation all work fine over HTTP.
- **One session, hardcoded.** No connection list, no multi-user, no session management —
  all things the Java webapp would have provided.
- **No clipboard, file transfer or session recording.** Those are webapp features too.
- **`navigator.maxTouchPoints` stays 0.** There is no touch hardware to report; sites that
  gate on it rather than on `(pointer: coarse)` will still see a desktop.
- **Ephemeral profile.** A fresh Firefox profile every start, deliberately: reproducible,
  but no history, cookies or open tabs survive a restart.

## Layout

```
app/                      the tunnel service (uv + starlette)
  src/guac_tunnel/
    protocol.py           instruction encode/decode, incremental parser
    handshake.py          select → args → size/audio/video/image → connect → ready
    bridge.py             the two asyncio pumps
    app.py                routes
    static/               the mobile client and the diagnostic page
  tests/                  65 tests, including a guacd stand-in on a real socket
containers/
  firefox/                Xvnc + openbox + a normal Firefox
  tunnel/                 the Python service
mise.toml                 tools, env and every task
```

## Tasks

| | |
|---|---|
| `build` · `build:firefox` · `build:tunnel` | images; `js:vendor` fetches guacamole-common-js |
| `up` · `down` · `restart` · `status` · `url` | the pod |
| `logs` · `logs:firefox` · `logs:guacd` · `logs:tunnel` | following output |
| `shell:firefox` · `shell:tunnel` · `vnc` | poking inside |
| `resize <w> <h>` | change the remote geometry |
| `test` · `lint` · `format` · `dev` | the Python side |
