# video-analyzer-cli

A small runnable example that plays a local video file with detected and tracked faces drawn
on it, via `ffplay`. Wires [`video-analyzer-core`](../core)'s real SCRFD/ArcFace/ByteTrack/PCHIP
backends into [`video-analyzer-kernel`](../kernel)'s `Pipeline`, and adds its own `ffplay`-piping
`FrameSink` plus a couple of no-op stubs (`SceneDetector`, `FrameBroadcaster`) that neither
`kernel` nor `core` implement.

```bash
uv run video-analyzer-cli ../app/assets/sample.mp4

# Watch it 4x faster. Every frame is still decoded and drawn; the speed-up is paid for
# with detection coverage (~1/4 of frames detected, interpolation fills the rest).
uv run video-analyzer-cli ../app/assets/sample.mp4 --speed-factor-target 4

# ...and spend a bigger detection batch per pass to buy some of that coverage back.
uv run video-analyzer-cli ../app/assets/sample.mp4 --speed-factor-target 4 --max-batch-frames 16
```

`--speed-factor-target`, `--max-batch-frames`, `--max-batch-lag-ms` and `--lookahead` are the
tuning flags; `--help` documents them all, and `CLAUDE.md` explains why the detection budget
deliberately does *not* scale with the speed factor.

Relative paths (video, `--scrfd-model`, `--arcface-model`) passed directly to `uv run` resolve
against this directory, since that's where it executes from. `mise run cli -- <video>` resolves
the video path against the repo root instead — see `CLAUDE.md` for details.
