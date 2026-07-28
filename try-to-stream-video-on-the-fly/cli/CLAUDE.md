# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this
subproject. See the repo root `CLAUDE.md` for how this fits alongside `app/`, `kernel/`, and
`core/`.

## What this is

`video-analyzer-cli` (importable as `video_analyzer.cli`) — a small runnable example that plays a
local video file with detected/tracked faces drawn on it, via `ffplay`. It's the first real
end-to-end consumer of [`kernel`](../kernel)'s `Pipeline` wired to [`core`](../core)'s SCRFD/
ArcFace/ByteTrack/PCHIP/ffmpeg backends, outside their own unit tests.

Depends on `core` via a `uv` path source (`../core`, editable), which transitively pulls in
`kernel` the same way. Not a workspace, matching the standalone-project style `app/`, `kernel`,
and `core` already use.

## Commands

All commands run from this directory (a `uv`-managed Python project).

```bash
uv sync                             # install/update dependencies (resolves ../core, ../kernel)
uv run video-analyzer-cli <video>   # play a local video file, faces drawn, via ffplay
uv run ruff check src               # lint
uv run ty check src                 # type check
```

Or via `mise` from the repo root: `mise run cli -- <video>`. Both `uv run` here and the mise task
run with `cli/` as the working directory (`uv --directory=cli`, matching `mise/tasks/webapp`'s own
convention for `app/`) — so a relative `<video>` path is resolved against `cli/`, not the repo
root or your shell's cwd; use `../app/assets/sample.mp4`-style relative paths or an absolute path.

## Architecture

```
src/video_analyzer/cli/
├── main.py                   click entry point: wires FfmpegFrameSource + OnnxFaceDetector +
│                              OnnxFaceEmbedder + ByteTrackTracker + PchipInterpolator (all from
│                              core) + this package's own FfplayFrameSink/stubs into
│                              kernel.Pipeline, then calls pipeline.drain(frame_source)
├── ffplay_frame_sink.py       FfplayFrameSink(kernel.FrameSink) — spawns `ffplay`, draws each
│                              frame's detections in-place (solid box = exact detection, dashed
│                              via core.overlay.draw_dashed_rect = interpolated), pipes raw BGR24
│                              bytes to its stdin. No encoding step — ffplay reads rawvideo
│                              directly. Owned here, not in core: core has no opinion on
│                              transport (see core/CLAUDE.md), and this is one.
├── noop_scene_detector.py     NoopSceneDetector(kernel.SceneDetector) — always returns False;
│                              satisfies Pipeline's constructor even though nothing calls
│                              detect_scene_cut yet and core has no scene-cut algorithm to port
├── noop_frame_broadcaster.py  NoopFrameBroadcaster(kernel.FrameBroadcaster) — no-op; this example
│                              has nothing else consuming detection metadata
└── system_clock.py            SystemClock(kernel.Clock) — time.monotonic(), since kernel.Clock is
                               sync and Pipeline's BatchGate calls it directly
```

ONNX weights are **not** bundled here (same as `core`) — `--scrfd-model`/`--arcface-model` default
to `../app/models/{scrfd_10g_kps_dynamic,arcface_w600k_r50_batch}.onnx`, the weights already in
the repo, but can point anywhere.

## Working in this codebase

- This project only composes `kernel`+`core` — it shouldn't grow algorithm code of its own beyond
  the `ffplay`-piping/drawing glue in `ffplay_frame_sink.py`. A new detector/tracker/interpolator
  backend belongs in `core`; a new service contract belongs in `kernel`.
- `OnnxFaceDetector`/`OnnxFaceEmbedder` are sync context managers (`OnnxModel.__enter__`/
  `__exit__` lazily load/release the ONNX session) — open both with a `with` block before calling
  `pipeline.drain()`, same as any other `core` consumer would.
- No test suite here on purpose — it's a thin wiring example; `kernel`/`core` already unit-test
  every piece it composes.
