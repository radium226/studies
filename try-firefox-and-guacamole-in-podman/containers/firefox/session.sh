#!/bin/bash
# The X session: a window manager, and Firefox filling the screen.
set -euo pipefail

readonly PROFILE=/tmp/firefox-profile

log() { printf '[session] %s\n' "$*" >&2; }

# openbox gives Firefox a window manager to be maximised by and to take
# keyboard focus from. Nothing else is ever on screen.
openbox --config-file /opt/openbox-rc.xml --startup /bin/true &

# A fresh profile every start: the experiment should be reproducible.
rm -rf "$PROFILE"
mkdir -p "$PROFILE"
sed -e "s|@DEVICE_PIXEL_RATIO@|${DEVICE_PIXEL_RATIO}|g" \
    -e "s|@USER_AGENT@|${USER_AGENT}|g" \
    /opt/firefox-user.js > "${PROFILE}/user.js"

# Firefox is a normal browser here -- real tabs, a real URL bar -- so it needs
# to be told to fill the screen. There is no command-line flag for that, but
# the window geometry lives in the profile, so seed it before first start.
# Sizes are CSS pixels, hence the division by the device pixel ratio.
width=$(awk "BEGIN { printf \"%d\", ${SCREEN_WIDTH} / ${DEVICE_PIXEL_RATIO} }")
height=$(awk "BEGIN { printf \"%d\", ${SCREEN_HEIGHT} / ${DEVICE_PIXEL_RATIO} }")
cat > "${PROFILE}/xulstore.json" <<JSON
{
  "chrome://browser/content/browser.xhtml": {
    "main-window": {
      "screenX": "0",
      "screenY": "0",
      "width": "${width}",
      "height": "${height}",
      "sizemode": "maximized"
    }
  }
}
JSON

log "opening ${START_URL} at ${width}x${height} css px"
exec firefox-esr \
    --profile "$PROFILE" \
    --no-remote \
    "$START_URL"
