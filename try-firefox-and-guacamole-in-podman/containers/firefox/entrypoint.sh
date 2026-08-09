#!/bin/bash
# Start Xvnc, wait for it to actually be usable, then hand over to the session.
set -euo pipefail

readonly DISPLAY_NUMBER=0
readonly X_SOCKET="/tmp/.X11-unix/X${DISPLAY_NUMBER}"

log() { printf '[firefox] %s\n' "$*" >&2; }

# The X socket and the VNC port appear at different moments. Racing either one
# means openbox or guacd connects to nothing and dies quietly, so wait for both.
wait_for() {
    local what=$1 check=$2 attempts=100
    while ! eval "$check"; do
        ((attempts--)) || { log "timed out waiting for $what"; exit 1; }
        sleep 0.1
    done
    log "$what is up"
}

cleanup() {
    log "shutting down"
    kill "${XVNC_PID:-}" "${SESSION_PID:-}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

log "starting Xvnc at ${SCREEN_WIDTH}x${SCREEN_HEIGHT} @${SCREEN_DPI}dpi"

# -SecurityTypes None: no VNC password at all. Only acceptable because 5900
#   never leaves the pod -- see the README.
# -AlwaysShared: a phone reconnecting must not evict the running session.
# -localhost=0: guacd reaches us over the pod's shared loopback.
Xvnc ":${DISPLAY_NUMBER}" \
    -geometry "${SCREEN_WIDTH}x${SCREEN_HEIGHT}" \
    -depth 24 \
    -dpi "${SCREEN_DPI}" \
    -SecurityTypes None \
    -AlwaysShared \
    -localhost=0 \
    -rfbport 5900 \
    -desktop firefox \
    &
XVNC_PID=$!

wait_for "the X socket" "[[ -S '${X_SOCKET}' ]]"
wait_for "the display" "xdpyinfo -display :${DISPLAY_NUMBER} >/dev/null 2>&1"

/usr/local/bin/session.sh &
SESSION_PID=$!

# Exit as soon as either half dies, rather than lingering as a container that
# is running but shows nothing.
wait -n "$XVNC_PID" "$SESSION_PID"
log "a child exited; stopping the container"
