#!/bin/bash
# Start the compositor, publish it, then run the browser on it.
#
# All three outlive any connection, which is the whole reason this container is
# built the way it is. wlroots owns one seat from the moment it starts, so a
# client that connects an hour later types into the same session -- and a client
# that disconnects takes nothing with it.
set -euo pipefail

readonly VNC_PORT=5900

log() { printf '[firefox] %s\n' "$*" >&2; }

wait_for() {
    local what=$1 check=$2 attempts=100
    while ! eval "$check"; do
        ((attempts--)) || { log "timed out waiting for $what"; exit 1; }
        sleep 0.1
    done
    log "$what is up"
}

# Reading the kernel's socket table rather than connecting to the port: a probe
# that opens a connection and immediately drops it is a failed RFB handshake at
# the other end, and every startup would log one.
# 0A is TCP_LISTEN; the port is hex in that file.
vnc_listening() {
    awk -v port="$(printf ':%04X$' "$VNC_PORT")" \
        '$4 == "0A" && $2 ~ port { found = 1 } END { exit found ? 0 : 1 }' \
        /proc/net/tcp /proc/net/tcp6
}

cleanup() {
    log "shutting down"
    kill "${SESSION_PID:-}" "${WAYVNC_PID:-}" "${SWAY_PID:-}" "${DBUS_PID:-}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# sway's config file cannot read the environment, so the environment is written
# into a copy of it instead.
sed -e "s|@SCREEN_WIDTH@|${SCREEN_WIDTH}|g" \
    -e "s|@SCREEN_HEIGHT@|${SCREEN_HEIGHT}|g" \
    -e "s|@KEYBOARD_LAYOUT@|${KEYBOARD_LAYOUT}|g" \
    /etc/sway/config > "${XDG_RUNTIME_DIR}/sway-config"

# Firefox looks for a session bus and spends a noticeable part of its startup
# failing to find one.
log "starting a session bus"
dbus-daemon --session --address="unix:path=${XDG_RUNTIME_DIR}/bus" --nofork --nopidfile &
DBUS_PID=$!
export DBUS_SESSION_BUS_ADDRESS="unix:path=${XDG_RUNTIME_DIR}/bus"

# The headless backend needs no DRM device, no VT and no seat manager, which is
# what lets all of this run as an ordinary user. WLR_LIBINPUT_NO_DEVICES stops
# wlroots refusing to start for want of a keyboard that a container will never
# have.
log "starting sway at ${SCREEN_WIDTH}x${SCREEN_HEIGHT}"
WLR_BACKENDS=headless WLR_LIBINPUT_NO_DEVICES=1 \
    sway --config "${XDG_RUNTIME_DIR}/sway-config" &
SWAY_PID=$!

wait_for "the Wayland socket" "[ -S ${XDG_RUNTIME_DIR}/${WAYLAND_DISPLAY} ]"

# No --render-cursor: guacd draws the pointer itself from the RFB cursor
# pseudo-encoding, and a second one painted into the framebuffer would trail
# behind it.
#
# --socket puts wayvnc's control socket next to sway's, on the volume the
# tunnel can see. The tunnel needs it to answer one question before every
# reshape -- is anybody still connected? -- because resizing the output under a
# connected client segfaults wayvnc 0.9.1 and takes this container with it.
log "starting wayvnc on ${VNC_PORT}"
wayvnc --socket="${WAYVNC_SOCKET}" 0.0.0.0 "${VNC_PORT}" &
WAYVNC_PID=$!

wait_for "the VNC port" vnc_listening

log "starting the session"
/usr/local/bin/session.sh &
SESSION_PID=$!

# Exit as soon as any of them dies, rather than lingering as a container that
# is running but has nothing to show.
wait -n "$DBUS_PID" "$SWAY_PID" "$WAYVNC_PID" "$SESSION_PID"
log "a child exited; stopping the container"
