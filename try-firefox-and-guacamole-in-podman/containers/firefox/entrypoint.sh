#!/bin/bash
# Start xrdp and its session manager, and wait for 3389 to actually answer.
#
# Nothing X-related happens here. Under RDP the session -- Xorg, openbox and
# Firefox -- is spawned by sesman when a client connects, so this container is
# running long before there is anything to look at. See startwm.sh.
set -euo pipefail

readonly RDP_PORT=3389

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
# that opens a connection and immediately drops it makes xrdp log a failed X.224
# handshake, so every startup would end in six ERROR lines that mean nothing.
# 0A is TCP_LISTEN; the port is hex in that file.
rdp_listening() {
    awk -v port="$(printf ':%04X$' "$RDP_PORT")" \
        '$4 == "0A" && $2 ~ port { found = 1 } END { exit found ? 0 : 1 }' \
        /proc/net/tcp /proc/net/tcp6
}

cleanup() {
    log "shutting down"
    kill "${SESMAN_PID:-}" "${XRDP_PID:-}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# sesman authenticates through PAM against /etc/shadow, so there is no
# equivalent of Xvnc's -SecurityTypes None: the session needs a real password.
# Setting it here rather than at build time keeps mise.toml the single source of
# truth for a credential the tunnel container has to match.
log "setting the session password for ${RDP_USERNAME}"
echo "${RDP_USERNAME}:${RDP_PASSWORD}" | chpasswd

# sesman starts the session with a sanitised environment, so none of the
# container's own variables reach startwm.sh by inheritance. This file is how
# they cross that gap.
cat > /etc/xrdp/session-env.sh <<ENV
export DEVICE_PIXEL_RATIO='${DEVICE_PIXEL_RATIO}'
export USER_AGENT='${USER_AGENT}'
export START_URL='${START_URL}'
ENV
chmod 644 /etc/xrdp/session-env.sh

log "starting xrdp-sesman"
xrdp-sesman --nodaemon &
SESMAN_PID=$!

log "starting xrdp on ${RDP_PORT}"
xrdp --nodaemon &
XRDP_PID=$!

# A container that is running but not yet accepting connections looks identical
# to a broken one from the outside, and guacd's first attempt is what would find
# out.
wait_for "the RDP port" rdp_listening

# Exit as soon as either half dies, rather than lingering as a container that is
# running but can never start a session.
wait -n "$SESMAN_PID" "$XRDP_PID"
log "a child exited; stopping the container"
