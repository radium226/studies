# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this
subproject. See the repo root `CLAUDE.md` for how this fits alongside `app/`, `kernel/`, and
`core/`.

## What this is

`video-analyzer-cli` (importable as `video_analyzer.cli`) — a small runnable example that plays a
local video file or an http(s) page URL (resolved to a direct media URL via `core`'s `yt-dlp`
resolver) with detected/tracked faces drawn on it, via `ffplay`, and can optionally replay
each discovered face track afterward (a `track_recording:` config section). It's the first real end-to-end consumer of
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
uv run video-analyzer-cli <url>     # same for an http(s) page URL (yt-dlp resolves it first)
uv run video-analyzer-cli --dump-config > run.yaml   # every knob, at its default
uv run video-analyzer-cli <video> --config run.yaml  # ...and run with it
uv run pytest                       # unit tests (tests/ — SOURCE argument handling only)
uv run ruff check src tests         # lint
uv run ty check src tests           # type check
```

There are exactly two options: `--config FILE` and `--dump-config`. All tuning lives in a
YAML document (`config.py`'s `CliConfig`), not in flags — `--dump-config` prints every section
and key filled in with the defaults a bare run uses, so `--dump-config > run.yaml` is how you
start one. Anything omitted from a document keeps its default; unknown keys are a hard error
naming the full path (`pipeline.batch_gate has unknown keys: max_framez`), surfaced as a
`click.BadParameter` rather than a traceback.

The schema, section by section:

- `models:` — `scrfd`/`arcface` ONNX weight paths (not bundled; default to `../app/models/...`).
- `pipeline:` — `kernel.PipelineConfig` verbatim: `batch_gate` (`max_frames`, `max_lag_ms`),
  `render_cursor` (`lookahead_snapshots`), `buffering` (`max_pending_frames`,
  `frame_channel_capacity`).
- `frame_source:` — `core.FfmpegFrameSourceConfig`. **`read_rate` is the playback speed factor**
  (4.0 = four times as fast, 0.5 = slow motion); see the note below for what it does and doesn't
  scale. `resize` takes an ffmpeg-style `[width, height]` pair with `-1` on one axis to preserve
  aspect — a knob the flag-based CLI never exposed.
- `frame_sink:` — this package's `FfplayFrameSinkConfig` (just `stop_timeout`; the sink's
  width/height/fps come from the probed source).
- `face_detector:`/`face_embedder:`/`tracker:`/`interpolator:` — the corresponding `core` configs.
  Note `tracker:` has no `fps` key: that is probed, so it is a constructor argument.
- `scene_detector:` — `core.HistogramSceneDetectorConfig`, or `null` to swap in the never-cuts
  `NoopSceneDetector` (the old `--no-scene-detection`). The pipeline calls `detect_scene_cut` on
  every frame pair either way; answering False means tracking runs straight through hard cuts.
- `stop_after_frame_count:` — `{max_frames: N}` stops early after N frames are read, via
  `core.StopAfterFrameCount` wrapping the `FrameSource`. Graceful: frames already read still
  drain all the way through the pipeline, same as natural end-of-stream (see `kernel.StopToken`).
  Absent/`null` = off.
- `stop_on_first_track:` — `{min_track_frames: N}` stops early once the first face track reaches
  N rendered frames, via `core.StopOnFirstTrack` wrapping the `FrameBroadcaster`. N is *the
  definition of a track* here, measured in video frames — exact detections and the interpolated
  frames between them count alike, which is exactly why the condition sits at the broadcast
  stage: interpolated frames only exist downstream of the render cursor, so a tracker-level
  condition could only ever count sparse detection snapshots. The stop is still graceful (same
  `kernel.StopToken` contract), so playback continues briefly past the trigger and the track
  keeps growing while the tail drains. Absent/`null` = off. Note `{}` gives `core`'s default of
  1, not the 30 the old `--stop-on-first-track` flag implied — spell the number you want.
- `track_recording:` — present means the old `--play-tracks`: after the main `ffplay` window
  closes, replay each discovered face track through its own window, one at a time, in track-id
  order, via `TrackRecordingFrameBroadcaster` (a real `FrameBroadcaster` — `core` ships none, see
  `core/CLAUDE.md`). Skipped entirely when the main window was *closed* rather than played to
  the end, and a closed track window ends the replay (see the closing-the-window bullet below).
  `crop_size` (default `160`) is the side length each crop is resized to;
  `max_crops_per_track` (default `300`) caps how many crops each track keeps — its *first* N
  rendered frames, ~10 s at 30 fps, ~22 MB per track at the defaults; `null` = unbounded.
  Absent/`null` = off.

The positional `SOURCE` is either a local video file or an `http://`/`https://` URL — a URL is
first resolved to a direct media URL via `core.resolve_direct_media_url` (the `yt-dlp` binary
must be on PATH, same subprocess convention as ffmpeg/ffprobe), then fed to `FfmpegFrameSource`
exactly like a file path. Anything that is neither an existing file nor an http(s) URL is
rejected up front with a `click.BadParameter`.

Or via `mise` from anywhere in the repo: `mise run cli -- <video>`. `uv run` here (and the mise
task's own call into it) runs with `cli/` as the working directory (`uv --directory=cli`, matching
`mise/tasks/webapp`'s own convention for `app/`) — so a relative `<video>` path passed directly to
`uv run video-analyzer-cli` (bypassing mise) is resolved against `cli/`, not the repo root or your
shell's cwd; use `../app/assets/sample.mp4`-style relative paths or an absolute path in that case.
The `mise run cli` path doesn't have this problem: `mise/tasks/cli` resolves its first non-flag
argument to an absolute path (via `realpath`, explicitly anchored at `${MISE_PROJECT_ROOT}`)
*before* handing it to `uv --directory=cli` (URLs are exempted and passed through untouched —
`realpath` would mangle them), so a `<video>` path relative to the repo root
survives that later directory change unchanged — regardless of which directory you actually ran
`mise run cli` from (mise itself always starts the task with `$MISE_PROJECT_ROOT` as `cwd`, not
your shell's cwd, which is exactly why the repo root — not "wherever you typed the command" — is
the right anchor here). **Only the first non-flag argument gets that treatment**, so a relative
`--config` path (and the `models:` paths inside it) still resolves against `cli/` even under
`mise run cli` — pass those absolute if you're not sitting in `cli/`. Same caveat the old
`--scrfd-model`/`--arcface-model` flags had.

## Architecture

```
src/video_analyzer/cli/
├── config.py                 CliConfig — the whole tuning surface as one YAML-backed
│                              document, composing kernel's and core's config trees with this
│                              package's own two, all on kernel's `Config` base. `--dump-config`
│                              prints `CliConfig()`; `--config` parses one back. Also home to
│                              FfplayFrameSinkConfig and TrackRecordingFrameBroadcasterConfig
├── main.py                   click entry point (SOURCE + --config/--dump-config, nothing else):
│                              resolves an http(s) SOURCE to a direct media
│                              URL via core.resolve_direct_media_url (yt-dlp), then wires
│                              FfmpegFrameSource + OnnxFaceDetector +
│                              OnnxFaceEmbedder + ByteTrackTracker + SplineInterpolator +
│                              HistogramSceneDetector (all from core) + this package's own
│                              FfplayFrameSink/stubs into kernel.Pipeline (everything composed on
│                              one AsyncExitStack), then calls pipeline.run(frame_source); if
│                              a track_recording section is present, follows up with
│                              _play_tracks() once the whole stack (incl. the decoder) has closed
├── ffplay_frame_sink.py       FfplayFrameSink(kernel.FrameSink) — spawns `ffplay`, draws each
│                              frame's detections onto a *copy* (solid box = exact detection,
│                              dashed via core.overlay.draw_dashed_rect = interpolated), pipes raw
│                              BGR24 bytes to its stdin. No encoding step — ffplay reads rawvideo
│                              directly. Owned here, not in core: core has no opinion on
│                              transport (see core/CLAUDE.md), and this is one; the subprocess
│                              plumbing reuses core.pipe_io (drain_stderr, terminate_and_wait).
│                              write_raw_frame() shows plain pixels without an AnnotatedFrame —
│                              _play_tracks() uses it for each track's replay window. Given a
│                              kernel.StopToken, a `_watch_for_exit` task ends the whole run when
│                              ffplay exits on its own (window closed / crash) and flips
│                              `has_exited`, which _play_tracks/_run read to stop opening windows.
├── track_recording_frame_broadcaster.py  TrackRecordingFrameBroadcaster(kernel.FrameBroadcaster)
│                              — a real (non-noop) FrameBroadcaster: crops+resizes tracked faces
│                              out of each rendered frame and buffers them by track_id in
│                              crops_by_track (up to config.max_crops_per_track each — the first
│                              N frames). Records every rendered frame a track appears in —
│                              interpolated and held frames as much as exact detections, each
│                              cropped at that frame's own (possibly interpolated) box
│                              (tests/test_track_recording_frame_broadcaster.py pins this). Only
│                              wired in when track_recording is configured; composes with
│                              stop_on_first_track (core.StopOnFirstTrack wraps *around* this
│                              recorder, delegating every frame before counting it), still
│                              recording whatever drains after the stop.
├── noop_scene_detector.py     NoopSceneDetector(kernel.SceneDetector) — always returns False;
│                              the `scene_detector: null` backend (the pipeline calls
│                              detect_scene_cut on every frame pair; answering False means
│                              tracking runs straight through hard cuts)
├── noop_frame_broadcaster.py  NoopFrameBroadcaster(kernel.FrameBroadcaster) — no-op; used whenever
│                              track_recording isn't configured and nothing else consumes
│                              metadata
└── system_clock.py            SystemClock(kernel.Clock) — time.monotonic(), since kernel.Clock is
                               sync and Pipeline's BatchGate calls it directly
```

ONNX weights are **not** bundled here (same as `core`) — the config's `models.scrfd`/
`models.arcface` default to `../app/models/{scrfd_10g_kps_dynamic,arcface_w600k_r50_batch}.onnx`,
the weights already in the repo, but can point anywhere.

## Working in this codebase

- This project only composes `kernel`+`core` — it shouldn't grow algorithm code of its own beyond
  the `ffplay`-piping/drawing glue in `ffplay_frame_sink.py` and the config document that names
  what to compose. A new detector/tracker/interpolator
  backend belongs in `core`; a new service contract belongs in `kernel`.
- **Never draw onto `annotated_frame.frame.content` directly.** `Pipeline` hands the same `Frame`
  object to the detection buffer and to the `FrameBroadcaster`, and `core`'s `FfmpegFrameSource`
  returns read-only views over the decoder pipe's `bytes` — so drawing in place both corrupts the
  other consumers (the detector would see burned-in boxes) and fails outright, `cv2` refusing a
  readonly output array. `FfplayFrameSink.write_frame` copies first; so does `app/engine.py`.
- **Closing the `ffplay` window stops the whole run.** The window is the only thing this CLI
  produces, so `FfplayFrameSink` is handed the same `kernel.StopToken` the early-stop wrappers
  get, and a `_watch_for_exit` task calls `request_stop()` the moment `ffplay` is reaped. It
  watches the *process* rather than relying on the write path failing, because a write to a dead
  pipe isn't reliably an error: asyncio's transport notices the `EPIPE` itself and then silently
  discards everything written afterwards, so `write_raw_frame` alone could feed a corpse for the
  rest of the video. The stop is the usual graceful one (frames already read drain through), and
  writes short-circuit on `has_exited`, so the tail costs nothing. `_shutdown` cancels the
  watcher before terminating anything — from there on, ffplay exiting is *us* ending it, not the
  user — and settles `has_exited` from `proc.returncode` first so a process that had already
  exited isn't misread as a normal teardown. At a natural end of stream ffplay is still parked on
  its stdin (`-autoexit` only fires at input EOF), so that check can't false-positive. `_run`
  reads `has_exited` to skip the track replay after a closed window, and `_play_tracks` reads it
  between tracks — otherwise the next track's window pops straight back up in place of the one
  just closed.
- `FfplayFrameSink` defaults `SDL_VIDEODRIVER=wayland` on a Wayland session (overridable — an
  explicit value always wins). Left to itself SDL picks its x11 driver and runs through XWayland,
  which measured **~6 fps** on a 720x1280 rawvideo stream versus exact realtime natively. Since this
  sink pushes uncompressed frames at video rate, that gap is the difference between smooth playback
  and the whole pipeline running under backpressure. If playback is inexplicably slow, check which
  driver SDL actually picked before suspecting the CV stages.
- **`frame_source.read_rate` is three separate knobs, and only two of them move.** The decoder's
  read rate goes up (frames arrive `N`× faster) and `FfplayFrameSink`'s fps goes up (they're shown
  `N`× faster) — but `Pipeline`'s `frames_per_second` argument stays at the file's **native** fps,
  and so does `core.ByteTrackTracker(fps)`. That is not an oversight, it's the whole mechanism:
  `frames_per_second` is only ever used to build `Pipeline.sample_and_detect`'s `BatchGate`, whose
  `TokenBucket(refill_rate=fps, capacity=1.0)` is a **wall-clock** budget charged
  `elapsed_seconds * fps` per pass. Note it is a constructor argument of `Pipeline`, not a config
  key — it's probed from the source, so a config *file* must not appear to offer it. Pinned to native fps, detection passes per wall-second stay put
  while `N`× more frames stream by, so ~`1/N` of frames get detected, `_sample_evenly` spreads its
  sample over an `N`× wider index range, and `RenderCursor` interpolates the wider gaps. Scaling it
  to `fps * N` instead would just push the inference duty cycle toward 100% for a marginal coverage
  gain. `app/` makes the same choice ("the CV `Engine` keeps native fps"). Everything downstream is
  **frame-index** based, not time based, so nothing else needs rescaling.
- **It's a *target*, not a guarantee, and deliberately un-clamped.** `kernel`'s frame-carrying
  channels are bounded (`_FRAME_CHANNEL_CAPACITY`), so a sink that can't keep up pushes backpressure
  through `produce_frames` into the decoder's pipe until `ffmpeg` blocks — the achieved multiplier
  silently caps at whatever the `.copy()` + cv2 draw + pipe write path sustains (~2.8 MB/frame at
  720x1280). Detection is *not* part of that cap: `kernel`'s `sample_and_detect` runs each pass as
  a background task and keeps draining its channel while the pass runs, so inference outlasting the
  frame interval widens the sampling stride instead of backpressuring the decoder — the sink path
  is the only thing that bounds the achieved speed. Likewise, `fps * N` above the monitor's
  refresh rate is left for SDL/ffplay's own frame-drop path to absorb: no warning, no clamp, and
  no decimation in the sink.
- **The config document never overrides a library default**, and `cli/config.py` says why at
  length: a `default_factory` on a `CliConfig` field only applies when that section is *absent*,
  so `frame_source: {read_rate: 4.0}` would silently drop `loop` back to `core`'s own default. A
  default that can't survive a partial document isn't representable, so where this CLI wanted a
  different value the *library* default was changed instead (`core`'s `loop=False`, `kernel`'s
  `lookahead_snapshots=3`). `tests/test_config.py` pins it.
- `OnnxFaceDetector`/`OnnxFaceEmbedder` are sync context managers (`OnnxModel.__enter__`/
  `__exit__` lazily load/release the ONNX session) — `_run` enters them on the same
  `AsyncExitStack` as the async ffmpeg/ffplay contexts (`enter_context` vs
  `enter_async_context`), which is exactly what the stack is for.
- The test suite (`tests/`) is deliberately minimal — it's a thin wiring example; `kernel`/`core`
  already unit-test every piece it composes. The only tests here cover glue this package itself
  owns: `main()`'s command-line surface (file-or-URL validation, `--dump-config` without a SOURCE,
  a bad `--config` becoming a usage error — `_run` is stubbed out, so no pipeline, ffmpeg, or
  yt-dlp runs), `CliConfig`'s composition (`tests/test_config.py` — the document *is* CLI-owned
  logic now, and the partial-section trap above needs pinning), and
  `TrackRecordingFrameBroadcaster`'s recording of interpolated/held frames. Don't grow it beyond
  that.
- `TrackRecordingFrameBroadcaster.crops_by_track` is bounded *per track* by
  `track_recording.max_crops_per_track` (default `300` crops — a track's first N rendered frames;
  `null` records everything). Note the bound is per track, not global: total memory still grows
  with the number of *distinct* track ids a long video accumulates, so `stop_after_frame_count`/
  `stop_on_first_track` remain the way to hard-bound a whole `track_recording` run.
- `_play_tracks` is sequential, one `ffplay` window per track, closed before the next opens — not
  because concurrent windows can't work, but because it keeps the demo simple and avoids
  contending with the main video's own `ffplay` process for the Wayland/X11 session.
