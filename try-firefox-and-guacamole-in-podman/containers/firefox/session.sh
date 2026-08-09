#!/bin/bash
# The X session: a window manager, and Firefox filling the screen.
set -euo pipefail

readonly PROFILE=/tmp/firefox-profile

log() { printf '[session] %s\n' "$*" >&2; }

# openbox is here only so Firefox gets a well-behaved fullscreen and keyboard
# focus; nothing is ever visible except the browser.
openbox --startup /bin/true &

# A fresh profile every start: the experiment should be reproducible, and there
# is nothing worth keeping in a kiosk.
rm -rf "$PROFILE"
mkdir -p "$PROFILE"
sed -e "s|@DEVICE_PIXEL_RATIO@|${DEVICE_PIXEL_RATIO}|g" \
    -e "s|@USER_AGENT@|${USER_AGENT}|g" \
    /opt/firefox-user.js > "${PROFILE}/user.js"

log "opening ${KIOSK_URL}"
exec firefox-esr \
    --profile "$PROFILE" \
    --no-remote \
    --kiosk \
    "$KIOSK_URL"
