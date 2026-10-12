#!/usr/bin/env bash
# Generate a 10s 1080p H.264 test clip (no binary checked into git)
set -euo pipefail

output="${1:-input.mp4}"
ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i "testsrc2=size=1920x1080:rate=30:duration=10" \
  -c:v libx264 -pix_fmt yuv420p \
  "${output}"
echo "${output}"
