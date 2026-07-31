# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this
subproject. See the repo root `CLAUDE.md` for how this fits alongside `app/`, `kernel/`, `core/`,
and `cli/`.

## What this is

`video-analyzer-webapp` (importable as `video_analyzer.webapp`) — `app/`'s browser-facing product
(pick a video, watch it stream live over HTTP via MSE while detected/tracked faces are drawn on
it, with a scrollable left-hand column showing a live, ever-growing face-loop video per tracked
face), rebuilt on [`kernel`](../kernel)'s `Pipeline` and [`core`](../core)'s real SCRFD/ArcFace/
ByteTrack/spline/ffmpeg backends, the same way [`cli`](../cli) is — except `cli` shows the result
in a local `ffplay` window and this project streams it to a browser instead. That transport layer
(an fMP4 encoder sink with overlay drawing, an HTTP fan-out broadcaster, a second fan-out per
tracked face, the Starlette routes and web UI) is this project's own contribution; neither
`kernel` nor `core` has an opinion on it (see `core/CLAUDE.md`).

Depends on `core` via a `uv` path source (`../core`, editable), which transitively pulls in
`kernel`. Not a workspace, matching the standalone-project style `app/`, `kernel`, `core`, and
`cli` already use.

## Commands

All commands run from this directory (a `uv`-managed Python project).

```bash
uv sync                                  # install/update dependencies (resolves ../core, ../kernel)
uv run video-analyzer-webapp             # http://127.0.0.1:8000, defaults
uv run video-analyzer-webapp --dump-config > run.yaml   # every knob, at its default
uv run video-analyzer-webapp --config run.yaml          # ...and run with it
uv run pytest                            # unit tests (tests/ — pure-Python units, no ffmpeg/ONNX)
uv run ruff check src tests              # lint
uv run ty check src                      # type check
```

Or via `mise` from anywhere in the repo: `mise run webapp` (`mise run app` runs the older `app/`
implementation — see the root `CLAUDE.md`).

There are exactly two CLI options: `--config FILE` and `--dump-config`, same as `cli`. Unlike
`cli`, there's no positional source argument and no `stop_strategy:` config section — the app
**starts idle** (no source, no pipeline), and the video file, the speed factor, and the stop
strategy are all chosen live from the web UI per request (`POST /api/source`), not fixed for the
whole process. Everything else — model paths, pipeline batching/lookahead, frame source/sink
tuning, which directory is browsable, server host/port — lives in the YAML document.

The schema, section by section (see `config.py`'s `WebappConfig`):

- `models:` — `scrfd`/`arcface` ONNX weight paths (not bundled; default to `../app/models/...`).
- `pipeline:` — `kernel.PipelineConfig` verbatim, same as `cli`.
- `frame_source:` — `core.FfmpegFrameSourceConfig` (`loop`, `resize`, `read_rate`, `stop_timeout`).
  `read_rate` (the playback speed factor) is the one field of this section overridden per request
  — `PipelineManager._build` builds the effective config via `dataclasses.replace(self._config
  .frame_source, read_rate=speed_factor)`, so `loop`/`resize`/`stop_timeout` still come from here
  unconditionally. Unlike `app/`'s original UI, `loop` is **not** exposed per request — it stays a
  static YAML-only choice.
- `frame_sink:` — `core.FfmpegFrameSinkConfig` directly (unlike `cli`, which needs its own
  `FfplayFrameSinkConfig` because its sink isn't `core`'s ffmpeg encoder — this project's sink
  *is*, via `overlay_frame_sink.py`).
- `face_detector:`/`face_embedder:`/`tracker:`/`interpolator:` — the corresponding `core` configs.
- `scene_detector:` — `core.HistogramSceneDetectorConfig`, or `null` for the never-cuts
  `NoopSceneDetector`, same idiom as `cli`.
- `video_library:` — `directory:` only, and it's purely the default directory the browse modal
  (`/api/browse`) opens on first use — not an access boundary; the modal can navigate anywhere the
  process can read. Which extensions count as a video is **not** configurable here:
  `kernel.Config`'s parser only supports fixed-length tuples (`tuple[int, int]`-style), not an
  open-ended list, so the allowed extension set lives as a module constant
  (`fs_browser.VIDEO_EXTENSIONS`) instead — closer to a format allowlist than a per-run tuning
  choice anyway.
- `broadcaster:` — `max_fragments`, how many recent fMP4 fragments the HTTP broadcaster retains
  for new/lagging clients (`app/`'s hardcoded default of 15, promoted to a config field).
- `server:` — `host`/`port` for uvicorn.

The stop strategy — `core.StopStrategyConfig`'s `after_frame_count`/`on_first_track`, exactly as
`core/CLAUDE.md` documents — is parsed straight from the `POST /api/source` JSON body
(`core.StopStrategyConfig.from_dict(body.get("stop_strategy"))`, reusing `core`'s own validation)
each time a source starts, not from the YAML. `speed_factor` (default `1.0` if omitted) is
validated by hand in `app.py` (must parse as a number, must be `> 0`) before ever calling
`PipelineManager.start` — there's no `core` config object to delegate to here, since a bare
`read_rate` isn't itself a `Config` subclass.

### System dependencies

Same as `core`/`cli`: **`ffmpeg`** (decode + encode) must be on `PATH`. No `yt-dlp` dependency —
this project only plays local files from `video_library.directory`, no URL/synthetic sources.

## Architecture

```
core.FfmpegFrameSource.start()  -- decodes the chosen file, probes VideoInfo
        |
        v
kernel.Pipeline.run(frame_source, stop_token)   -- the whole CV pipeline: detect/embed/track/
        |                                          interpolate, in six concurrent stages
        v  (per rendered frame, via write_and_broadcast -- both consumers get the same
        |   AnnotatedFrame)
        +-----------------------------------------------------+
        v                                                     v
OverlayFrameSink.write_frame(annotated_frame)     TrackVideoManager.broadcast_frame(annotated_frame)
        |  copies the frame, draws boxes+track ids,                 |  per new track_id: spins up its own
        |  hands it to core.FfmpegFrameSink                         |  core.FfmpegFrameSink + Broadcaster;
        v                                                           |  every tick, every started track
core.FfmpegFrameSink  -- encodes to fragmented MP4                  |  gets one frame: a fresh
        |  (baseline H.264, same MSE-tuned flags as                 |  letterboxed crop if present, else
        |  app/writer.py's _encoder_cmd)                            v  the next step of a ping-pong bounce
        v  (Orchestrator's box-parse task)                (one more core.FfmpegFrameSink per track)
iso_bmff.pump_fragments()  -- splits raw ffmpeg stdout into                |  (same pump_fragments(), one
        |  top-level ISO BMFF boxes, pairs moof+mdat                      |   pump task per track)
        v                                                                 v
broadcaster.Broadcaster  -- init segment (ftyp..moov) +      broadcaster.Broadcaster (one per track)
        |  bounded deque of moof+mdat frags                          |
        v                                                            v
Starlette GET /{stream_id}.mp4         Starlette GET /videos/{stream_id}/{track_id}.mp4
        |  StreamingResponse: init + latest frag, then live-tail (both routes share the
        |  _stream_broadcaster() helper)                                    |
        v                                                                    v
static/player.js -- MediaSource + SourceBuffer         static/tracks.js -- /ws/tracks announces each
   (via static/live-stream.js's attachLiveStream())       track_id, then one <video> per track, also
                                                            via attachLiveStream()
```

Key files (`src/video_analyzer/webapp/`):

- **`config.py`** — `WebappConfig`: the YAML-backed document described above, on `kernel.Config`.
- **`fs_browser.py`** — the server side of the "choose a file from anywhere" browse modal, with
  deliberately **no access boundary** beyond "must be a real, readable directory/existing video
  file" (an acceptable posture only because this is a local single-user tool — see the repo root
  `CLAUDE.md`). `list_directory(path, default)` lists the directories and recognized video files
  directly under `path` (or `default` — the configured `video_library.directory` — when `path` is
  empty, i.e. the modal's first open), sorted case-insensitively with directories first; raises
  `ValueError` for anything that isn't a readable, existing directory. `resolve_video_path(path)`
  validates a path picked in the modal: must be absolute, must have a recognized video extension,
  must resolve to an existing file. The web UI only ever POSTs a path it already got from
  `list_directory` via `/api/browse`, but the request could still be forged, so
  `resolve_video_path` re-validates from scratch rather than trusting the client.
- **`overlay_frame_sink.py`** — `OverlayFrameSink(kernel.FrameSink)`: the one piece neither
  `kernel` nor `core` provides. `core.FfmpegFrameSink.write_frame` takes a full `AnnotatedFrame`
  and draws nothing (see its own module docstring — overlay drawing is explicitly left to the
  composing app, same as `cli/ffplay_frame_sink.py` does it inline for its own sink). This class
  copies the frame, draws boxes/track-ids via `core.overlay.draw_dashed_rect`/`cv2` (solid = exact
  detection, dashed = interpolated — same convention `cli`'s sink uses), and delegates the rest
  (`write_frame`/`close_stdin`/`read_output_chunk`) to a wrapped `core.FfmpegFrameSink`.
- **`orchestrator.py`** — `Orchestrator`: owns two long-lived asyncio tasks, created with plain
  `create_task` (not a `TaskGroup` — a `TaskGroup` held open across the context-manager yield would
  cancel whichever unrelated task entered the context when a child crashes, same reasoning
  `app/orchestrator.py` documents). `_run_pipeline` is just `await pipeline.run(frame_source,
  stop_token=...)` — `kernel.Pipeline.run()` already *is* the CV pipeline, so there's no
  hand-rolled forwarding loop like `app/orchestrator.py`'s `_forward_frames` — with a `finally:
  await frame_sink.close_stdin()` to guarantee the encoder gets EOF'd (flushing trailing
  fragments) whether `run()` returns cleanly, raises, or is cancelled during teardown.
  `_read_sink_output` is the box-parse loop, logic identical to `app/orchestrator.py`'s.
  **`_describe_exception`** unwraps `BaseExceptionGroup` recursively: `kernel.Pipeline.run()` runs
  its six stages in an `asyncio.TaskGroup`, so a crash always arrives wrapped in one — even for a
  single failing stage — and its own `str()` is just "unhandled errors in a TaskGroup (1
  sub-exception)". `failure_reason` uses this instead of a bare `str()` so the browser's error
  popup says something useful.
- **`track_video_manager.py`** — `TrackVideoManager(kernel.FrameBroadcaster)`: the *other* consumer
  `write_and_broadcast` hands every `AnnotatedFrame` to, alongside `OverlayFrameSink`. On a
  track_id's first appearance it spins up a dedicated `core.FfmpegFrameSink` + `Broadcaster` +
  `pump_fragments()` task for it (same shape as the main stream, just scoped to one face) and runs
  forever after that — no per-track teardown, only whole-pipeline teardown (the manager's own
  `AsyncExitStack`). Every `_TrackStream` also owns a bounded `deque` (`_LOOP_BUFFER_FRAMES`, its
  most recent crops) plus a replay position/direction pair for a ping-pong bounce through it. Every
  tick (one `broadcast_frame` call per rendered frame), every started track gets **exactly one**
  frame written: if the track is actually present this tick, its bounding box is cropped to a
  padded square (letterboxed into a fixed thumbnail via `_letterbox_resize` if edge-clamping made
  the actual crop region non-square — never squashed/distorted), appended to the buffer
  (`record_live_crop`, which also arms the bounce to resume from the previous frame next time it
  goes idle), and written straight through; if it's *not* present (occluded, out of frame, lost by
  the tracker), `next_replay_frame()` advances one step of the bounce and that frame is written
  instead. So a live track streams its real crops, and once it goes quiet it seamlessly starts
  bouncing back and forth through its own recent history — oldest↔newest, forever, at the same fps
  — rather than the encoder stalling or jump-cutting on every wrap. Writing every tick regardless
  (not skipping quiet ticks) is also what keeps each per-track connection's byte rate steady enough
  that the browser's own idle timeout (`live-stream.js`'s `IDLE_TIMEOUT_MS`) never fires.
  `subscribe()`/`unsubscribe()` back a
  `/ws/tracks` websocket: a new subscriber gets every track id already seen, then every subsequent
  one live; a `None` sentinel on teardown unblocks anyone still parked on a queue.
  `has_track`/`get_broadcaster` back `app.py`'s per-track routes.
- **`pipeline_manager.py`** — `PipelineManager`: same overall shape as `app/pipeline.py` (teardown-
  first `AsyncExitStack` rebuild, monotonic `_generation` counter, a per-build watchdog task
  awaiting `broadcaster.wait_closed()`, `SourceError`/`PipelineStatus` TypedDicts, `status()`), but
  composing `kernel.Pipeline` + `core`'s real backends instead of `app/`'s own `Engine`/
  `InputVideoLoader`, and taking an already-resolved `source_path: Path`, a `speed_factor: float`,
  and a per-request `core.StopStrategyConfig`. Resolving/validating all three is `app.py`'s job
  (via `fs_browser` for the path, by hand for the speed factor, via `core.StopStrategyConfig
  .from_dict` for the stop strategy) — this class trusts every argument it's handed, the same way
  `app/app.py`'s URL format check runs before calling into the manager at all. `_build` is
  teardown-first from the start, same reasoning as `app/pipeline.py`: only one ffmpeg pair + ONNX
  pipeline ever runs at a time. Builds the effective `core.FfmpegFrameSourceConfig` via
  `dataclasses.replace(self._config.frame_source, read_rate=speed_factor)`, and scales the
  encoder's own fps by the same factor (`fps * speed_factor`) — the other half of the time
  compression, same as `app/`'s and `cli`'s `--speed-factor`/`read_rate`. Constructs
  `core.StopAfterFrameCount`/`core.StopOnFirstTrack` decorators only when the per-request
  `stop_strategy` arms them (same "enabled is composition metadata" rule `core/CLAUDE.md`
  documents). Unlike `cli`'s `NoopFrameBroadcaster`, this project's `kernel.FrameBroadcaster` is a
  real consumer: `TrackVideoManager` (entered into the same `new_stack`, fps matching the main
  encoder's `fps * speed_factor`), optionally wrapped by `core.StopOnFirstTrack` when armed. Also
  sets `self._app.state.track_manager` alongside `broadcaster`/`stream_id` (and clears it in
  `_set_idle_state`), so `app.py`'s track routes see the same idle/stale-generation semantics for
  free.
- **`broadcaster.py`** — `Broadcaster`: ported from `app/broadcaster.py`, unchanged. Single
  producer (the box-parse task) / many async consumers on one `asyncio.Condition`; bounded `deque`
  drop-oldest retention; `wait_for_next()` raises `LaggedError` when a client falls off the back;
  `snapshot_for_new_client()` gives a new client the init segment + only the latest fragment (true
  live join, not rewind).
- **`iso_bmff.py`** — `BoxReader`: ported from `app/iso_bmff.py`, unchanged. Minimal ISO BMFF box
  parser; pipe reads never align to box boundaries. Also owns `pump_fragments(read_chunk,
  broadcaster)`: the moof/mdat-pairing loop that drives a `Broadcaster` from an ffmpeg encoder's
  raw stdout, extracted so `Orchestrator._read_sink_output` (the main stream) and every per-track
  pump task in `track_video_manager.py` share one implementation instead of two copies of the same
  box-pairing logic.
- **`noop_scene_detector.py`**, **`system_clock.py`** — trivial local copies of `cli`'s versions of
  the same name. `webapp` can't depend on `cli` (a sibling standalone project, not a dependency),
  so these few-line classes are re-created rather than imported.
- **`app.py`** — Starlette wiring + `click` CLI. Routes: `/` (player page), `/{stream_id}.mp4`
  (main live tail, ported from `app/app.py`'s `stream()` — same `410 Gone` contract: no pipeline,
  superseded `stream_id`, or an already-closed broadcaster), `/videos/{stream_id}/{track_id}.mp4`
  (per-track live tail, same staleness check against `stream_id` plus an unknown/closed-track
  check against `track_manager.get_broadcaster`), `/api/status` (`PipelineManager.status()`),
  `/api/tracks/{track_id}` (404 if `track_manager` is `None` or the id is unknown, else
  `{"video_url": "/videos/{stream_id}/{track_id}.mp4"}`), `/ws/tracks` (`WebSocketRoute`:
  `track_manager.subscribe()`, sends `{"event": "existing", "track_ids": [...]}` then
  `{"event": "new_track", "track_id": n}` per queue item until a `None` sentinel or disconnect;
  closes immediately if idle), `/api/browse` (`GET ?path=...`, defaults to
  `config.video_library.directory` when omitted; `{"path", "parent", "entries": [{"name", "path",
  "is_dir", "size", "modified"}, ...]}` from `fs_browser.list_directory` — a bad/unreadable path is
  400, not a 500), `POST /api/source` (body `{"path", "speed_factor", "stop_strategy": {...}}`;
  resolves the path via `fs_browser.resolve_video_path`, validates `speed_factor` by hand (must
  parse as a number, must be `> 0`; defaults to `1.0` if omitted), and validates the stop strategy
  via `core.StopStrategyConfig.from_dict` — all **before** calling `PipelineManager.start`, so any
  of the three failing 400s without ever touching ffmpeg or tearing down whatever is currently
  playing; 60 s timeout on the build itself → 504; other build failures → 400), `POST /api/stop`
  (go idle). The main and per-track live-tail routes share `_stream_broadcaster(request,
  broadcaster)` — the `generate()`/`StreamingResponse` construction is identical either way, only
  which `Broadcaster` differs. No `/metrics` route: `kernel.Pipeline` has no metrics-snapshot
  facility today (unlike `app/`'s `Engine.metrics_snapshot()`) — a real scope cut versus `app/`'s
  UI, not an oversight. `_configure_logging()` (called inside `main()`, not at import time, so
  importing this module for tests has no logging side effects) calls `logger.enable
  ("video_analyzer")`: `kernel`/`core`/`webapp` each disable their own logger by default (library
  etiquette — see `kernel/CLAUDE.md`), so the entry point has to opt back in, same as `cli/main.py`
  does. Needs `websockets` (or `wsproto`) installed for uvicorn's `WebSocketRoute` support — it's a
  declared dependency (`pyproject.toml`), not implied by `starlette`/`uvicorn` alone.
- **`static/live-stream.js`** — `attachLiveStream(videoEl, streamUrl, {onStreaming, onEnded})`: the
  MediaSource/SourceBuffer live-tail plumbing (append queue, quota-eviction, buffer trim,
  live-edge seek) every stream route's client needs, extracted out of `player.js` so both the main
  player and every per-track `<video>` in `tracks.js` share one implementation. One-shot per call
  (creates and later revokes its own object URL); callers wanting reconnect-on-drop call it again.
- **`static/player.js`** — adapted from `app/`'s (previously reused verbatim; now calls
  `attachLiveStream` instead of inlining the MediaSource logic itself) — still only depends on
  `/api/status`'s shape and a stream URL, so it stays source-agnostic. `onEnded(gone)`'s `gone`
  flag reproduces the original's two distinct endings: a clean 410/EOF close (show idle, poll
  `/api/status` again) versus a dropped connection (show "reconnecting...", retry sooner).
- **`static/tracks.js`** — the face-loop column: opens `/ws/tracks`, and for every `existing`/
  `new_track` track id not already rendered, `GET /api/tracks/{id}` for its `video_url`, creates a
  small muted `<video class="track-entry">` in `#tracks-column`, and calls `attachLiveStream` on
  it (no `onStreaming`/`onEnded` needed — a track's stream runs for the pipeline's whole lifetime,
  and the socket closing already signals "clear everything"). A socket close (idle, or the
  manager tearing down on stop/rebuild) clears every entry and reconnects on a timer, same
  `player.js` idle-retry idiom.
- **`static/browse-modal.js`** — the file-browser modal: `window.openBrowseModal(onSelect)` opens
  it (fetching `/api/browse` with no `path`, i.e. the configured default directory), renders
  folders/files from the listing, clicking a folder navigates into it, clicking a file calls
  `onSelect(absolutePath)` and closes the modal. A path input doubles as breadcrumb display and a
  jump-to-path field (Enter navigates there); an Up button uses the listing's `parent`.
- **`static/source.js`** — adapted from `app/`'s: a "Choose file…" button opens the browse modal
  instead of a URL text input or a directory dropdown; the selected absolute path is held in a
  local variable and shown next to the button; form submit builds the
  `{path, speed_factor, stop_strategy}` body above. The "Test pattern" button is dropped (no
  synthetic source).

## Working in this codebase

- **This project only composes `kernel`+`core` plus a transport layer** — it shouldn't grow
  algorithm code of its own beyond `overlay_frame_sink.py`'s drawing glue and
  `track_video_manager.py`'s crop/resize (same category: presentation, not detection/tracking
  math). A new detector/tracker/interpolator backend belongs in `core`; a new service contract
  belongs in `kernel`.
- **`core.FfmpegFrameSink` draws nothing; `OverlayFrameSink` is the only place that does it.**
  Never bypass it and call `core.FfmpegFrameSink` directly if you want boxes burned into the video.
- **`kernel.FrameBroadcaster` and this project's own `Broadcaster` are conceptually unrelated, even
  though `TrackVideoManager` is now both at once.** `FrameBroadcaster` is the CV pipeline's
  per-frame metadata sink contract (`TrackVideoManager` implements it, optionally wrapped by
  `core.StopOnFirstTrack` when that strategy is armed); `Broadcaster` is the HTTP fMP4 byte fan-out
  to browsers (`TrackVideoManager` owns one *instance* of it per track, entirely separate from the
  `Broadcaster` the main stream uses). Don't conflate the two when reading `pipeline_manager.py` or
  `track_video_manager.py`.
- **Every per-track stream is a second, independent copy of the main stream's plumbing** — its own
  `core.FfmpegFrameSink`, its own `Broadcaster`, its own `pump_fragments()` task — just fed cropped
  thumbnails instead of full frames. It starts on first detection and runs until the *whole
  pipeline* tears down (no per-track idle timeout, no per-track disposal when the tracker loses
  it). If you're tempted to add a per-track teardown to bound resource usage with many
  simultaneous tracks, that's a deliberate scope cut for this local single-user tool, not a gap to
  silently close.
- **A quiet track bounces through its own recent history rather than stalling or jump-cutting.**
  `_TrackStream.buffer` (capped at `_LOOP_BUFFER_FRAMES`) holds a track's most recent crops;
  `broadcast_frame` writes a fresh crop when the track is present, or
  `stream.next_replay_frame()` when it isn't — every tick, unconditionally. `next_replay_frame`
  is a **ping-pong bounce** (oldest↔newest, reversing at both ends), not a forward-only cycle back
  to index 0: that's what makes the loop fluid — every step, including the ones at either end,
  differs from the last by exactly one buffered frame, so it never teleports. `record_live_crop`
  arms the bounce to resume *backward* from the just-appended frame the next time the track goes
  idle (not forward from the oldest buffered frame), so even the very first idle tick after a live
  stretch continues smoothly rather than jump-cutting into the loop. Don't special-case "track is
  present but its crop is None" (a box clipped to nothing) differently from "track absent
  entirely" — both fall through to the same replay path, on purpose, so the write cadence never
  skips a tick. If you change any of this, keep the "one write per started track per tick"
  invariant: a tick that goes by with no write for some track is exactly what makes its
  browser-side connection look stalled and hit `live-stream.js`'s idle timeout.
- **Crops are letterboxed, never squashed, to protect the aspect ratio.** `_crop_padded_square`'s
  target region is square, but clamping it to the frame's bounds near an edge can make the actual
  crop non-square again (clipped on one axis, not the other). `_letterbox_resize` scales that
  region to fit `_THUMBNAIL_SIZE` on its longer side and centers it on a black canvas rather than
  stretching it to fill the square — a face near the frame edge should look correctly proportioned
  with black bars, not stretched. It's a no-op (no visible bars) whenever the crop is already
  square, which is the common, unclamped case.
- **A crash out of `kernel.Pipeline.run()` is a `BaseExceptionGroup`, always** — even for exactly
  one failing stage, since the six stages run in an `asyncio.TaskGroup`. `orchestrator
  ._describe_exception` unwraps it recursively; if you add another layer that catches a pipeline
  failure, route it through the same helper (or an equivalent) rather than a bare `str(exc)`, or
  the browser popup degrades to "unhandled errors in a TaskGroup (1 sub-exception)".
- **Validate before tearing down.** `app.py`'s `set_source` resolves the path (`fs_browser
  .resolve_video_path`) and the stop strategy **before** calling `manager.start(...)` at all — a
  bad request shouldn't stop whatever is currently playing. `PipelineManager` itself no longer
  does any resolution or validation; it trusts the `Path` it's handed. If you add more
  per-request validation, put it in `app.py` ahead of the `manager.start(...)` call, not inside
  `PipelineManager._build`.
- **There is deliberately no directory allowlist any more.** `fs_browser.resolve_video_path`
  accepts any absolute path to an existing, recognized-extension file — the browse modal can reach
  anywhere the process can read. `video_library.directory` is only ever a *default starting point*
  for the modal, not an access boundary; don't reintroduce a "must be inside this directory" check
  expecting it to be a security control, since the modal already lets an operator navigate past it
  freely, and this is a local single-user tool where that's an accepted tradeoff (see the repo
  root `CLAUDE.md`).
- **Video file extensions are a module constant (`fs_browser.VIDEO_EXTENSIONS`), not a config
  field.** `kernel.Config`'s parser only supports fixed-length tuples (it zips a YAML list against
  a fixed set of field annotations), not an open-ended `list[str]`/`tuple[str, ...]` — see
  `kernel/config.py`'s `_parse_value` if you're tempted to add one.
- Encoder settings are identical to `app/writer.py`'s and `core.FfmpegFrameSink`'s own — baseline
  profile, `frag_keyframe+empty_moov+default_base_moof`, a GOP aligned to a fixed keyframe
  interval. The browser MIME in `player.js` (`avc1.42001e`) must keep matching if either changes.
- Testing boundary matches every sibling project's own convention: tests are pure-Python units
  with no real `ffmpeg` subprocess or ONNX model file. `test_pipeline_manager.py` monkeypatches
  `core.FfmpegFrameSource.start`, `core.OnnxFaceDetector`/`OnnxFaceEmbedder`, and
  `OverlayFrameSink.start` to lightweight fakes (mirroring `app/tests/test_pipeline.py`'s style) —
  `core.ByteTrackTracker`/`HistogramSceneDetector`/`SplineInterpolator` are constructed for real
  since they need no external resource (same reasoning `core/CLAUDE.md` gives for testing
  `ByteTrackTracker` directly). Its fakes never emit a face, so they don't exercise
  `TrackVideoManager.broadcast_frame` — that lives in its own `test_track_video_manager.py`
  instead, which monkeypatches `core.FfmpegFrameSink.start` the same way and covers crop math
  (padding, clamping at frame edges, the outside-frame-returns-None case, the letterbox-not-squash
  case when clamping makes the region non-square), new-track subscribe/notify ordering (including
  the "subscribe backfills tracks seen before it connected" case), the `None`-sentinel teardown,
  and the idle-bounce behavior itself — asserting on the fake sink's `written_frames` that a quiet
  track keeps getting exactly one write per tick and that a multi-crop buffer bounces oldest↔newest
  in the exact order the ping-pong math predicts (not a forward-only cycle). `test_app.py` only
  covers requests that 400 on pure validation
  (bad path, relative path, malformed stop strategy) before ever reaching `PipelineManager.start`
  — pinned with an autouse fixture that monkeypatches `PipelineManager.start` to fail the test
  outright if it's ever called, so a validation check silently disappearing gets caught instead of
  the test passing for the wrong reason. A request that would actually build a pipeline needs real
  `ffmpeg`/ONNX weights and is manual/integration verification only (`mise run webapp` + a
  browser — for the face-loop column specifically, that means a source with an actual detectable
  face; the bundled `app/assets/sample.mp4` is a synthetic `testsrc` colorbar pattern with no
  faces in it, so it'll never populate the column). `test_fs_browser.py` exercises real arbitrary
  `tmp_path` locations (there's no allowlist to work around) and skips the unreadable-directory
  case when running as root (which bypasses permission bits).
