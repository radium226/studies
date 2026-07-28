# video-analyzer-cli

A small runnable example that plays a local video file with detected and tracked faces drawn
on it, via `ffplay`. Wires [`video-analyzer-core`](../core)'s real SCRFD/ArcFace/ByteTrack/PCHIP
backends into [`video-analyzer-kernel`](../kernel)'s `Pipeline`, and adds its own `ffplay`-piping
`FrameSink` plus a couple of no-op stubs (`SceneDetector`, `FrameBroadcaster`) that neither
`kernel` nor `core` implement.

```bash
uv run video-analyzer-cli ../app/assets/sample.mp4
```

Relative paths (video, `--scrfd-model`, `--arcface-model`) resolve against this directory, since
that's where `uv run`/`mise run cli` execute from — see `CLAUDE.md` for details.
