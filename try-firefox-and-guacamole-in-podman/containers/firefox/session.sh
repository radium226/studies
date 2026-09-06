#!/bin/bash
# The session: Firefox, as a Wayland client of the weston next door.
#
# Everything that used to be here besides this is gone. There is no window
# manager to start -- sway is one -- and no window geometry to compute, because
# a single undecorated window fills the workspace and follows the output when
# it changes shape. The environment arrives by plain inheritance now that no
# session manager scrubs it on the way in.
set -euo pipefail

readonly PROFILE=/tmp/firefox-profile

log() { printf '[session] %s\n' "$*" >&2; }

# A fresh profile every start: the experiment should be reproducible.
rm -rf "$PROFILE"
mkdir -p "$PROFILE"
sed -e "s|@DEVICE_PIXEL_RATIO@|${DEVICE_PIXEL_RATIO}|g" \
    -e "s|@USER_AGENT@|${USER_AGENT}|g" \
    /opt/firefox-user.js > "${PROFILE}/user.js"

log "opening ${START_URL}"
exec firefox-esr \
    --profile "$PROFILE" \
    --no-remote \
    "$START_URL"
