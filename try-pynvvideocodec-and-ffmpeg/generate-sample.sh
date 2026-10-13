#!/usr/bin/env bash
# Generate a 10s 1080p H.264 test clip (no binary checked into git), in BT.709
# and tagged as such: untagged, every tool guesses the RGB ⇄ YUV matrix
# differently (ffmpeg BT.601, PyNvVideoCodec BT.709 for HD)
set -euo pipefail

output="${1:-input.mp4}"
ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i "testsrc2=size=1920x1080:rate=30:duration=10" \
  -vf "scale=out_color_matrix=bt709:out_range=tv,setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv" \
  -c:v libx264 -pix_fmt yuv420p \
  -colorspace bt709 -color_primaries bt709 -color_trc bt709 -color_range tv \
  "${output}"
echo "${output}"
