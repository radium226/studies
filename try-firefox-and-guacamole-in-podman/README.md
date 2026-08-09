# Try Firefox and Guacamole in Podman

Run Firefox headlessly in a rootless Podman pod and drive it from a phone browser, with
the phone as the *primary* target rather than an afterthought.

The twist that makes this a study rather than a compose copy/paste: **the Java Guacamole
webapp is dropped entirely.** What stays is `guacd`, the C proxy that actually speaks RDP.
What replaces Tomcat is ~250 lines of Python that bridge a browser WebSocket to guacd's
wire protocol. Removing the webapp is also what makes genuinely zero-auth, single-URL
access possible: there is no login form to skip, because there is no auth subsystem.

| | |
|---|---|
| ![portrait](docs/client-portrait.png) | ![landscape](docs/client-landscape.png) |
| A 450×1000 CSS px phone viewport, and a remote session sized to match it: 1080×2400. | The same session after rotating. The remote reflowed to 2400×1080 — Firefox is genuinely landscape, not scaled down to fit. |

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
                   │        ▼ 127.0.0.1:3389   (rdp)              │
                   │ firefox (xrdp + Xorg + openbox + firefox)    │
                   └──────────────────────────────────────────────┘
```

Containers in a pod share a network namespace, so all three talk over loopback and only
port 8080 is published. That is what makes the RDP password in `mise.toml` a reasonable
thing to commit rather than a hole: 3389 is not reachable from anywhere but the pod.

There *is* a password, which there was not before. xrdp authenticates through PAM and has
no equivalent of Xvnc's `-SecurityTypes None`, so the credential is fixed, published, and
load-bearing only inside the pod.

## The protocol

Guacamole instructions are a comma-separated list of length-prefixed elements ending in a
semicolon; the first element is the opcode.

```
6.select,3.rdp;
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
client ──► 6.select,3.rdp;
guacd  ──► 4.args,13.VERSION_1_5_0,8.hostname,4.port,6.domain,8.username,…  (1503 chars, 81 parameters)
client ──► 4.size,4.1080,4.2400,2.96;
client ──► 5.audio,8.audio/L8,9.audio/L16;
client ──► 5.video;
client ──► 5.image,10.image/jpeg,9.image/png,10.image/webp;
client ──► 7.connect,13.VERSION_1_5_0,9.127.0.0.1,4.3389,0.,7.firefox,7.firefox,0.,…
guacd  ──► 5.ready,37.$8ba89709-996d-47d7-918c-67aeef19884c;

           …and the session stream begins:

guacd  ──► 5.mouse,1.0,1.0,10.1125589696,8.10707530;
guacd  ──► 4.size,2.-1,2.11,2.16;
guacd  ──► 3.img,1.1,2.12,2.-1,9.image/png,1.0,1.0;
guacd  ──► 4.blob,1.1,232.iVBORw0KGgoAAAANSUhEUgAAAAsAAAAQCAYAAADAvYV+…
guacd  ──► 3.end,1.1;
guacd  ──► 6.cursor,1.0,1.0,2.-1,1.0,1.0,2.11,2.16;
```

Three things in there are worth pausing on.

**`connect` is positional.** Its values line up with the parameter names guacd sent in
`args` — all 81 of them, in that order, with an empty string for each of the 72 we do not
set. Sending only the nine we care about connects to garbage rather than failing. RDP is
where this stops being theoretical: VNC asks for 44 parameters, RDP for 81.

**Slot 0 is a version, not a parameter.** From 1.5.0 guacd puts `VERSION_1_5_0` first in
`args`, and expects the negotiated version echoed back in that slot. Treating it as a
parameter name shifts every subsequent value by one.

**`ready` does not mean connected.** guacd answers `ready` before it has reached xrdp —
and under RDP it does not even mean the session exists, because sesman has still to
authenticate and start one. A failure there surfaces later as an `error` instruction
*inside* the session stream rather than as a handshake failure.

## Four things that bit

Each of these failed silently — no exception, no error log, just a black screen or a
wrong-sized one.

### The `dpi` in a `size` instruction is a divisor

To guacd's RDP client it is not metadata describing the display, it is arithmetic: the
pixel dimensions you asked for are rescaled by `96/dpi` before the server ever sees them.
Asking for 1080×2400 at this phone's real 230 dpi produces a 560×1252 session — not an
error, not a warning, just a session that is the wrong size and a client that letterboxes
it without knowing why.

Measured across a sweep, holding the request at 1080×2400:

| dpi sent | session created |
|---|---|
| 96 | 1080×2400 |
| 144 | 720×1600 |
| 192 | 540×1200 |
| 230 | 560×1252 |

96 is the only value that means what it says. Every bit of scaling in this study is
explicit and lives somewhere else — `layout.css.devPixelsPerPx` inside Firefox,
`display.scale()` in the client — so the tunnel now always sends 96, and the pixels
requested are the pixels allocated.

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
the client asks for a framebuffer in *physical* pixels, exactly as a phone panel reports
them — a 450×1000 viewport at a device pixel ratio of 2.4 means 1080×2400 — and
`layout.css.devPixelsPerPx = 2.4` divides it back down, landing exactly on that floor. It
is also why the client clamps its request at 1080 wide: below that, no ratio saves it.

**The pointer capability bitfield is `Coarse=1, Fine=2, Hover=4`.** Setting
`ui.primaryPointerCapabilities` to 2 for "coarse" quietly reports a mouse, and every site
that branches on `(pointer: coarse)` serves its desktop layout.

**Enabling touch events does not expose `ontouchstart`.** `dom.w3c_touch_events.enabled`
turns the events on, but the property that feature detection actually reads stays
undefined until `dom.w3c_touch_events.legacy_apis.enabled` is set too.

Firefox runs as an ordinary browser here — tabs, URL bar, menu, all navigable from the
phone — so it has to be *told* to fill the screen, and there is no command-line flag for
that. Two pieces: the window geometry lives in the profile, so `startwm.sh` seeds
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

RDP does have a multi-touch channel, but nothing on this path uses it: guacd is driven as
a mouse, and a mouse is what Firefox sees. So every gesture has to be translated.
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

## What RDP bought, and what it cost

This study ran on VNC first, and the reason to leave it was one line of Guacamole
documentation: `resize-method=display-update` is **RDP-only**. Over VNC there is no
client-driven dynamic resize, so rotating the phone could not reflow the remote session —
it could only rescale it. The same 1080×2400 session rendered at 38% in portrait and 15%
in landscape, letterboxed, with a `mise run resize` escape hatch that recreated the
container to change geometry for real.

Now rotation asks and the session obliges. The browser sends a `size` instruction
mid-session, guacd turns it into a Display Control PDU, xorgxrdp does a RandR resize,
openbox re-maximises and Firefox reflows. 1080×2400 becomes 2400×1080 and back, in about
30 ms of server time, at 42% both ways — the scale no longer moves, because the session is
the shape of the screen. The escape hatch is gone; there is nothing left for it to do.

Three things that were not free:

**The container is not short any more.** Xvnc was the X server and the VNC server in one
process. xrdp is three moving parts — xrdp on 3389, `xrdp-sesman` authenticating through
PAM, and an Xorg with the xorgxrdp driver started per session — which brings back exactly
what the VNC version was chosen to avoid: a session manager, PAM, a Unix password,
`Xwrapper.config`, generated RSA and TLS keys, and a container that runs as root. Rootless
Podman maps that root to an ordinary host user, which is the only reason it is tolerable.

**Firefox no longer runs until someone connects.** sesman starts the session on the first
RDP login, so `mise run up` leaves a pod with no browser in it. That also inverts where
geometry comes from: the framebuffer is negotiated by the client rather than configured,
so `SCREEN_WIDTH`/`SCREEN_HEIGHT` are now only a fallback, and `startwm.sh` reads the real
size back out of `xdpyinfo` to seed Firefox's window rather than computing it from
environment variables that no longer decide anything.

**A resize sent too early is dropped in silence.** If the request arrives while the
session is still coming up, xorgxrdp discards it and says nothing — and the first rotation
after opening the page is precisely when that happens, reproducibly. The client repeats
the request until the session reports the size it asked for, or four attempts run out.
Giving up is safe, because `fit()` still letterboxes; that is only what the VNC version
always did.

One thing that did not change: the requested width is clamped to 1080 physical pixels,
because Firefox will not size its window below 450 CSS px and 450 × 2.4 is 1080. A phone
with a device pixel ratio of 2 would otherwise ask for a session too narrow to render
into, and clip the right edge of every page. Below the clamp, the display letterboxes
again — the old behaviour, kept for the case that still needs it.

## Limits

- **No auth on the way in.** Anyone on the LAN who opens the URL gets a live browser. That
  is a deliberate choice for an experiment, not an oversight. The RDP credential between
  the tunnel and xrdp is not an exception to that: it is a fixed value in `mise.toml`,
  required because xrdp has no passwordless mode, and it protects a port that never leaves
  the pod.
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
  tests/                  74 tests, including a guacd stand-in on a real socket
containers/
  firefox/                xrdp + Xorg + openbox + a normal Firefox
  tunnel/                 the Python service
mise.toml                 tools, env and every task
```

## Tasks

| | |
|---|---|
| `build` · `build:firefox` · `build:tunnel` | images; `js:vendor` fetches guacamole-common-js |
| `up` · `down` · `restart` · `status` · `url` | the pod |
| `logs` · `logs:firefox` · `logs:guacd` · `logs:tunnel` | following output |
| `shell:firefox` · `shell:tunnel` · `rdp` | poking inside |
| `test` · `lint` · `format` · `dev` | the Python side |
