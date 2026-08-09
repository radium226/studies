# Wayland instead of X11: what was measured

This branch is an investigation, not a proposal. It does not work, and the
reason it does not work is worth more than the code.

The question was whether the session container could drop X11. Everything in it
that is heavy is X11's: `xorgxrdp`, `xserver-xorg-core`, `xserver-xorg-legacy`,
xrdp, `xrdp-sesman`, `Xwrapper.config`, a PAM password in `/etc/shadow`, a
`tsusers` group, and a container that runs as root. None of that is wanted. All
of it is the price of one feature — the RDP **Display Control channel**, which
is what makes the session reflow when the phone rotates instead of letterboxing,
and which is the reason this study left VNC in the first place.

Two Wayland architectures were built and measured against a real guacd. Both
render Firefox correctly. Neither can do the one thing the study exists to do.

## Attempt 1 — weston, with its built-in RDP backend

`weston --backend=rdp-backend.so --shell=kiosk-shell.so`, Firefox as a Wayland
client. The whole X11 stack collapses to one unprivileged process: no sesman, no
PAM, no password, no `Xwrapper.config`, no root. Debian trixie has weston 14.0.2
with `rdp-backend.so` in `libweston-14-0`.

It renders. It reflows. And then three things:

**guacd 1.5.5 draws nothing at all.** It connects, negotiates TLS, carries mouse
input into the compositor, reports completed frames every 250 ms — and never
paints a pixel. A FreeRDP 3 client against the same weston, at the same moment,
gets a perfect picture. 1.5.5 is built against FreeRDP 2; weston 14 links
FreeRDP 3.15. guacd **1.6.0** is the first on FreeRDP 3 and works immediately.
Not a codec problem: `--no-remotefx-codec` and colour depths 16/24/32 all
behave identically.

**There is no Display Control channel.** `libweston/backend-rdp/rdpdisp.c` does
implement `handle_adjust_monitor_layout()`, but it is reached only from
FreeRDP's `AdjustMonitorsLayout` peer callback — capability exchange. Nothing in
weston 14 *or* 16 opens a `DispServerContext`. So `resize-method=display-update`
does not silently fail; it ends the connection: `Aborted. See logs.`
`resize-method=reconnect` does work, and is cheaper here than it sounds — weston
and Firefox are not the connection, they outlive it — measured at **1.1 to 2.1
seconds** per rotation against xorgxrdp's ~30 ms.

**And then the one that ends it: weston creates a `wl_seat` per RDP peer.**
With no client connected there is no seat at all, so a Firefox started on an
empty compositor comes up against a compositor with no pointer and no keyboard —
GTK says so, in a stream of `gdk_seat_get_keyboard: assertion 'GDK_IS_SEAT
(seat)' failed` — and then ignores every click and keystroke for the rest of its
life, while rendering perfectly the whole time.

Waiting for a seat before starting Firefox fixes that, at the cost of the
property the swap was supposed to win back: the session starts on the first
connection again, exactly as it did under sesman. But it only defers the
problem. The seat belongs to *that* peer. Typed into the URL bar from the first
connection and then from a second: the bar reads `firstpeer`. The second
connection is mute — and since `reconnect` is the only resize that works, the
first rotation is itself a new peer, so rotating the phone permanently kills
input.

## Attempt 2 — sway (headless wlroots) + wayvnc

wlroots owns `seat0` from the moment it starts, independently of any client.
That is the right architecture, and it demonstrably fixes the input problem:
first connection types `firstpeer`, second connection types `secondpeer`, and
the URL bar reads `secondpeer`. Any client, any time, for the life of the
session.

guacd 1.6 also brought client-driven resize to VNC (GUACAMOLE-1196), which is
the feature whose absence sent this study to RDP. wayvnc has automatic resizing
of headless outputs on by default (`-R` disables it). On paper the whole thing
lines up.

It does not work in either direction:

- **Client asks.** guacd requests the right encodings — wayvnc logs
  `set encodings: ... desktop-size,extended-desktop-size` — but when the browser
  asks for a new size, guacd stops at `Screen data has not been initialized,
  yet.` / `Failed to send desktop size message.` It will not send
  `SetDesktopSize` until the server has first sent it an `ExtendedDesktopSize`
  rectangle, and wayvnc does not send one for a size that has not changed.
- **Server tells.** Changing the output out from under it — `swaymsg output
  HEADLESS-1 resolution 1080x2401` — gets `Error handling message from VNC
  server` and guacd drops the connection outright.

So the session cannot change shape from either end. TigerVNC with
`-RemoteResize=1` does not resize it either, which puts at least part of that on
wayvnc rather than on guacd.

## Where that leaves it

| | X11 (`main`) | weston/RDP | sway/wayvnc |
|---|---|---|---|
| renders | yes | yes, with guacd ≥ 1.6 | yes |
| input from any connection | yes | **first connection only** | yes |
| session outlives the connection | no | yes | yes |
| rotation reflows | yes, ~30 ms | yes, 1–2 s, then input dies | **no** |
| runs unprivileged, no PAM | no | yes | yes |

Wayland is not the obstacle. Both compositors do their half correctly; Firefox
on Wayland was never in question. What is missing is on the remote-desktop side
of each: weston never implemented the Display Control channel and ties its seat
to a peer, and wayvnc and guacd disagree about who announces a desktop size
first.

There is one design left that would work, and it was not built: keep sway, do
the resize *between* connections rather than during one — the browser closes the
tunnel on rotation, something resizes the sway output over its IPC socket, and
the browser reconnects into a session that is already the new shape. sway's seat
survives all of it, so input keeps working, and Firefox keeps its page. It costs
a reconnect per rotation and a control path from the tunnel into the session
container, which is a real addition to the architecture rather than a swap.

## What is on this branch

The sway + wayvnc build, complete and running: it renders, it accepts input from
any connection, and it cannot rotate. `containers/firefox/` is sway, wayvnc and
a session script; the tunnel speaks VNC to guacd 1.6. The weston/RDP build is in
this branch's history.

One fix here is worth keeping regardless of any of the above:
`Settings.from_env` read its fallbacks off the class, and `@dataclass(slots=True)`
makes every class attribute a slot descriptor — so with a variable unset, guacd
was asked for a protocol named `<member 'remote_protocol' of 'Settings'
objects>`. Only reachable outside the container, which is exactly where
`uv run guac-tunnel` runs.
