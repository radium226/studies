#!/bin/bash
# The X session: a window manager, and Firefox filling the screen.
#
# Run by xrdp-sesman, once per RDP connection, on the Xorg it has just started.
set -euo pipefail

readonly PROFILE=/tmp/firefox-profile

log() { printf '[session] %s\n' "$*" >&2; }

# sesman scrubs the environment, so DEVICE_PIXEL_RATIO, USER_AGENT and
# START_URL arrive through this file rather than by inheritance. Written by the
# entrypoint from the container's own environment.
# shellcheck source=/dev/null
source /etc/xrdp/session-env.sh

# openbox gives Firefox a window manager to be maximised by and to take
# keyboard focus from. Nothing else is ever on screen. It also listens for
# RandR screen changes and re-maximises, which is what turns a phone rotating
# into a reflow rather than a letterbox.
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
#
# The framebuffer size is whatever the RDP client negotiated, not something this
# container chose, so read it back off the running display rather than computing
# it from environment variables that no longer decide anything. Sizes are CSS
# pixels, hence the division by the device pixel ratio.
read -r width height < <(
    xdpyinfo | awk -v ratio="${DEVICE_PIXEL_RATIO}" '
        /dimensions:/ {
            split($2, size, "x")
            printf "%d %d\n", size[1] / ratio, size[2] / ratio
            exit
        }'
)
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
