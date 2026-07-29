# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this
subproject. See the repo root `CLAUDE.md` for how this fits alongside `app/`, `kernel/`, and
`core/`.

## What this is

`video-analyzer-cli` (importable as `video_analyzer.cli`) — a small runnable example that plays a
local video file with detected/tracked faces drawn on it, via `ffplay`, and can optionally replay
each discovered face track afterward (`--play-tracks`). It's the first real end-to-end consumer of
[`kernel`](../kernel)'s `Pipeline` wired to [`core`](../core)'s SCRFD/ArcFace/ByteTrack/spline/
ffmpeg backends, outside their own unit tests.

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

Tuning flags (all optional; the defaults reproduce plain native-speed playback):

- `--speed-factor-target N` — play the clip `N`× faster (`0.5` = slow motion). Scales the decoder
  read-rate (`ffmpeg -readrate N`) *and* ffplay's `-framerate` (`N * native_fps`), so playback stays
  time-balanced. Every frame is still decoded and drawn; what pays for the speed-up is **detection
  coverage** (see the note below).
- `--max-batch-frames N` — frames per batched SCRFD pass (`kernel`'s `batching.max_frames`,
  default `4`). The companion knob to `--speed-factor-target`: raise it to buy coverage back.
- `--max-batch-lag-ms T` — hold a batch up to `T` ms waiting for it to fill
  (`batching.max_lag_ms`, default `0` = fire immediately).
- `--lookahead K` — interpolation lookahead in detection snapshots (`rendering.lookahead_snapshots`,
  default `3`).
- `--scene-detection/--no-scene-detection` — on by default: `core.HistogramSceneDetector` flags
  hard cuts, and the pipeline resets face tracking at each one (identities and interpolation
  never bridge scenes). `--no-scene-detection` swaps in the never-cuts `NoopSceneDetector`.
- `--stop-after-frames N` — stop early after N frames are read, via `core.StopAfterFrameCount`
  wrapping the `FrameSource`. Graceful: frames already read still drain all the way through the
  pipeline, same as natural end-of-stream (see `kernel.StopToken`).
- `--stop-on-face-found` — stop early the first time a face is detected, via
  `core.StopOnFirstAnnotation` wrapping the `FrameBroadcaster`. Since that's the last stage before
  output, the stop only takes effect once the triggering frame has gone all the way through
  detection/tracking/interpolation/`--lookahead`, so a few extra frames may still play past the
  actual first detection.
- `--play-tracks` — after the main video's `ffplay` window closes, replay each discovered face
  track through its own `ffplay` window, one track at a time, in track-id order, via
  `TrackRecordingFrameBroadcaster` (a real `FrameBroadcaster` — `core` ships none, see
  `core/CLAUDE.md`). Every rendered frame's faces are cropped and buffered in memory for the whole
  run, so this trades RAM for the replay; `--track-crop-size` (default `160`) controls the
  side length each crop is resized to.

Or via `mise` from anywhere in the repo: `mise run cli -- <video>`. `uv run` here (and the mise
task's own call into it) runs with `cli/` as the working directory (`uv --directory=cli`, matching
`mise/tasks/webapp`'s own convention for `app/`) — so a relative `<video>` path passed directly to
`uv run video-analyzer-cli` (bypassing mise) is resolved against `cli/`, not the repo root or your
shell's cwd; use `../app/assets/sample.mp4`-style relative paths or an absolute path in that case.
The `mise run cli` path doesn't have this problem: `mise/tasks/cli` resolves its first non-flag
argument to an absolute path (via `realpath`, explicitly anchored at `${MISE_PROJECT_ROOT}`)
*before* handing it to `uv --directory=cli`, so a `<video>` path relative to the repo root
survives that later directory change unchanged — regardless of which directory you actually ran
`mise run cli` from (mise itself always starts the task with `$MISE_PROJECT_ROOT` as `cwd`, not
your shell's cwd, which is exactly why the repo root — not "wherever you typed the command" — is
the right anchor here).

## Architecture

```
src/video_analyzer/cli/
├── main.py                   click entry point: wires FfmpegFrameSource + OnnxFaceDetector +
│                              OnnxFaceEmbedder + ByteTrackTracker + SplineInterpolator +
│                              HistogramSceneDetector (all from core) + this package's own
│                              FfplayFrameSink/stubs into kernel.Pipeline (everything composed on
│                              one AsyncExitStack), then calls pipeline.run(frame_source); if
│                              --play-tracks, follows up with _play_tracks() once the whole stack
│                              (including the decoder) has closed
├── ffplay_frame_sink.py       FfplayFrameSink(kernel.FrameSink) — spawns `ffplay`, draws each
│                              frame's detections onto a *copy* (solid box = exact detection,
│                              dashed via core.overlay.draw_dashed_rect = interpolated), pipes raw
│                              BGR24 bytes to its stdin. No encoding step — ffplay reads rawvideo
│                              directly. Owned here, not in core: core has no opinion on
│                              transport (see core/CLAUDE.md), and this is one; the subprocess
│                              plumbing reuses core.pipe_io (drain_stderr, terminate_and_wait).
│                              write_raw_frame() shows plain pixels without an AnnotatedFrame —
│                              _play_tracks() uses it for each track's replay window.
├── track_recording_frame_broadcaster.py  TrackRecordingFrameBroadcaster(kernel.FrameBroadcaster)
│                              — a real (non-noop) FrameBroadcaster: crops+resizes every tracked
│                              face out of each rendered frame and buffers it by track_id in
│                              crops_by_track. Only wired in when --play-tracks is passed; still
│                              composes with --stop-on-face-found (core.StopOnFirstAnnotation
│                              wraps it, same "wrapped" decorator convention as stop_after_frame_count
│                              in core).
├── noop_scene_detector.py     NoopSceneDetector(kernel.SceneDetector) — always returns False;
│                              the --no-scene-detection backend (the pipeline calls
│                              detect_scene_cut on every frame pair; answering False means
│                              tracking runs straight through hard cuts)
├── noop_frame_broadcaster.py  NoopFrameBroadcaster(kernel.FrameBroadcaster) — no-op; used whenever
│                              --play-tracks isn't passed and nothing else consumes detection
│                              metadata
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
- **Never draw onto `annotated_frame.frame.content` directly.** `Pipeline` hands the same `Frame`
  object to the detection buffer and to the `FrameBroadcaster`, and `core`'s `FfmpegFrameSource`
  returns read-only views over the decoder pipe's `bytes` — so drawing in place both corrupts the
  other consumers (the detector would see burned-in boxes) and fails outright, `cv2` refusing a
  readonly output array. `FfplayFrameSink.write_frame` copies first; so does `app/engine.py`.
- `FfplayFrameSink` defaults `SDL_VIDEODRIVER=wayland` on a Wayland session (overridable — an
  explicit value always wins). Left to itself SDL picks its x11 driver and runs through XWayland,
  which measured **~6 fps** on a 720x1280 rawvideo stream versus exact realtime natively. Since this
  sink pushes uncompressed frames at video rate, that gap is the difference between smooth playback
  and the whole pipeline running under backpressure. If playback is inexplicably slow, check which
  driver SDL actually picked before suspecting the CV stages.
- **`--speed-factor-target` is three separate knobs, and only two of them move.** The decoder's
  `read_rate` goes up (frames arrive `N`× faster) and `FfplayFrameSink`'s fps goes up (they're shown
  `N`× faster) — but `PipelineConfig.frames_per_second` stays at the file's **native** fps, and so
  does `core.ByteTrackTracker(fps)`. That is not an oversight, it's the whole mechanism:
  `frames_per_second` is only ever used to build `Pipeline.sample_and_detect`'s `BatchGate`, whose
  `TokenBucket(capacity=1.0, refill_rate=fps)` is a **wall-clock** budget charged
  `elapsed_seconds * fps` per pass. Pinned to native fps, detection passes per wall-second stay put
  while `N`× more frames stream by, so ~`1/N` of frames get detected, `_sample_evenly` spreads its
  sample over an `N`× wider index range, and `RenderCursor` interpolates the wider gaps. Scaling it
  to `fps * N` instead would just push the inference duty cycle toward 100% for a marginal coverage
  gain. `app/` makes the same choice ("the CV `Engine` keeps native fps"). Everything downstream is
  **frame-index** based, not time based, so nothing else needs rescaling.
- **It's a *target*, not a guarantee, and deliberately un-clamped.** `kernel`'s frame-carrying
  channels are bounded (`_FRAME_CHANNEL_CAPACITY`), so a sink that can't keep up pushes backpressure
  through `produce_frames` into the decoder's pipe until `ffmpeg` blocks — the achieved multiplier
  silently caps at whatever the `.copy()` + cv2 draw + pipe write path sustains (~2.8 MB/frame at
  720x1280). Likewise, `fps * N` above the monitor's refresh rate is left for SDL/ffplay's own
  frame-drop path to absorb: no warning, no clamp, and no decimation in the sink.
- `OnnxFaceDetector`/`OnnxFaceEmbedder` are sync context managers (`OnnxModel.__enter__`/
  `__exit__` lazily load/release the ONNX session) — `_run` enters them on the same
  `AsyncExitStack` as the async ffmpeg/ffplay contexts (`enter_context` vs
  `enter_async_context`), which is exactly what the stack is for.
- No test suite here on purpose — it's a thin wiring example; `kernel`/`core` already unit-test
  every piece it composes.
- `TrackRecordingFrameBroadcaster.crops_by_track` grows for the entire run — nothing prunes it,
  unlike every bounded buffer in `kernel`/`core`. Deliberate for a study-project example (`--stop-
  after-frames`/`--stop-on-face-found` are the practical way to bound a `--play-tracks` run today),
  but a long, face-heavy video will use a lot of RAM.
- `_play_tracks` is sequential, one `ffplay` window per track, closed before the next opens — not
  because concurrent windows can't work, but because it keeps the demo simple and avoids
  contending with the main video's own `ffplay` process for the Wayland/X11 session.
