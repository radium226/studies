# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A study project: stream a video "live" over HTTP by continuously re-encoding it into fragmented
MP4 and pushing fragments to browsers via the MSE (Media Source Extensions) API — no HLS/DASH,
just a raw byte stream over a single long-lived HTTP response. On top of that transport, every
frame runs through a real computer-vision pipeline: face **detection** (SCRFD), face **embedding**
(ArcFace), multi-object **tracking** (ByteTrack), and per-frame **spline interpolation** of the
sparse detections so overlays stay smooth at full video frame rate.

See `README.md` for a very detailed, box-by-box explanation of the pipeline (every coordinate
space, rescale, and buffering point). Keep the two in sync when the pipeline changes.

## Commands

All commands run from the `app/` directory (a `uv`-managed Python project) unless noted.

```bash
uv run video-streamer         # run the app directly (synthetic input by default)
mise run webapp               # from repo root: runs the app against a URL source via yt-dlp
uv sync                       # install/update dependencies from uv.lock
uv run pytest                 # unit tests (tests/ — pure-Python units, no ffmpeg/ONNX needed)
uv run ruff check src tests   # lint (config in pyproject.toml)
uv run ty check src           # type check
```

The app **starts idle** (no source, no pipeline). A source is chosen at runtime from the player
page — paste a URL, hit "Test pattern" for the synthetic `testsrc` asset, or "Stop" to go back to
idle. There are no CLI source flags; the flags below are global tuning knobs only.

Useful CLI flags (see `app.py:main`):

- `--resize-video WxH` — scale frames in the decoder before any processing (accepts `-1` on one
  axis to preserve aspect ratio, ffmpeg-style).
- `--speed-factor N` — playback speed multiplier (default `1.0`). Scales *both* the decoder
  read-rate (`ffmpeg -readrate N`, always applied; `-readrate 1` == `-re`) and the encoder output
  fps (`N * native_fps`), so the browser sees a smooth, live-balanced `N×` fast-forward. Every
  frame is still decoded and encoded — the speed-up is time-compression, not frame dropping.
  Fractional values (`0.5`) give slow motion. The CV `Engine` keeps native fps.
- `--frag-duration-ms` — target fMP4 fragment duration (`ffmpeg -frag_duration`, in µs internally).
- `--scrfd-batch-frames N` — frames per batched SCRFD detection pass (default `4`). Each detection
  cycle samples up to N frames evenly spread across the interval since the last pass and runs them
  through SCRFD as one `(N,3,640,640)` batch, giving gapless real-detection coverage.
- `--arcface-batch-crops M` — max face crops per batched ArcFace pass (default `8`). All faces found
  across the N sampled frames are flattened into one embedding batch, chunked at M crops.

The app listens on `http://127.0.0.1:8000`. The player page has a source form that POSTs to
`/api/source` (a URL with an optional `loop` checkbox, or `synthetic: true` for the test pattern),
building the pipeline from idle; a Stop button POSTs to `/api/stop` to tear back down to idle. There
is no CI; run `pytest`/`ruff`/`ty` manually.

### System dependencies

These are invoked as subprocesses (not Python packages) and must be on `PATH`:

- **`ffmpeg` / `ffprobe`** — decode, encode, probe, and generate the synthetic sample asset.
- **`yt-dlp`** — only for URL sources; resolves the page URL to a direct media URL.

ONNX model weights live in `app/models/` (`scrfd_10g_kps_dynamic.onnx`,
`arcface_w600k_r50_batch.onnx`) and are loaded by `detection.py` via `onnxruntime`
(CPU execution provider).

## Architecture

The process is **fully asyncio** end to end. Two `ffmpeg` subprocesses (decode, encode) bracket
an in-process CV `Engine`; the only place work leaves the event loop is ONNX inference, offloaded
to a single-worker `ThreadPoolExecutor`. Everything is composed as async context managers
(`.start()` classmethods) held in a `PipelineManager` (`pipeline.py`) `AsyncExitStack`, so one
shared pipeline serves all clients and the whole stack can be torn down and rebuilt at runtime.
The process **starts idle** (no pipeline at all); a source arrives via `POST /api/source` and is
torn back down to idle when it ends, fails, or is stopped (`POST /api/stop`). Builds/rebuilds are
**teardown-first** (old stack fully closed before the new one starts — one ffmpeg pair + ONNX
engine at a time); clients bridge the gap by polling, driven by the `410 Gone` contract on the
live stream endpoint (see below).

```
ffmpeg decoder (loop/readrate source -> optional scale filter -> raw BGR24 on stdout) reader.py
   -> InputVideoLoader.frames(): read_exact -> numpy reshape (H,W,3) BGR24          input_video.py
   -> Engine.process(frame):                                                        engine.py
        * store frame in a delay buffer (pending_frames)
        * (throttled by a token bucket) run BATCHED detection off-thread on up to N
          PRISTINE frames sampled since the last pass:
              FaceDetector.detect_batch (SCRFD 640x640 letterbox) -> boxes+landmarks  detection.py
              FaceEmbedder.embed_many (ArcFace 112x112 aligned crop, M/batch) -> unit embeddings  detection.py
        * ByteTracker.update(dets, embs) per sampled frame -> stable track ids      tracking.py
        * LookaheadTrackBuffer: PCHIP-interpolate tracks per video frame,           interpolation.py
              emitting coords for a PAST frame (fixed lookahead lag)
        * draw overlay + boxes/landmarks/ids on the matching past frame             overlay.py
   -> Writer.write_frame(): raw BGR24 -> encoder stdin                              writer.py
ffmpeg encoder (BGR24 -> yuv420p H.264 baseline -> fragmented MP4 on stdout)        writer.py
   -> Orchestrator box-parse task: BoxReader splits stdout into top-level boxes     orchestrator.py + iso_bmff.py
   -> Broadcaster: init segment (ftyp..moov) + bounded deque of moof+mdat frags     broadcaster.py
   -> Starlette StreamingResponse per client: init + latest frag, then live-tail    app.py
   -> browser: MediaSource + SourceBuffer appends the byte stream, seeks to edge    static/player.js
```

Key files (`app/src/video_streamer/`):

- **`reader.py`** — async wrapper over the **decoder** ffmpeg process only. `probe_video_info()`
  uses `ffprobe` for width/height/fps. Applies the optional `scale=` resize filter. Hands out raw
  BGR24 frame bytes one at a time; knows nothing about the rest of the pipeline.
- **`writer.py`** — async wrapper over the **encoder** ffmpeg process (symmetric to `reader.py`).
  Accepts raw BGR24 on stdin, exposes the fMP4 byte stream on stdout. `_encoder_cmd` is tuned for
  MSE: baseline H.264, `yuv420p`, `zerolatency`, GOP = `fps * KEYFRAME_INTERVAL_SECONDS`, forced
  keyframes, `frag_keyframe+empty_moov+default_base_moof`, `-frag_duration`.
- **`input_video.py`** — `InputVideoLoader` ABC with `Synthetic` and `Url` strategies; owns a
  `Reader`, resolves post-resize dimensions (`_resolve_resize`), yields `(H,W,3)` BGR24 frames.
- **`engine.py`** — the CV heart. Runs detection **asynchronously and sparsely** (throttled by a
  `TokenBucket`) in **batches** of up to `scrfd_batch_frames` pristine frames sampled evenly across
  the interval since the last pass (`_sample_batch_frames` → `_detect_batch`), tracks each sampled
  frame in order and interpolates the results, and draws overlays onto a *delayed* frame
  (`pending_frames`) so the interpolation lookahead can never extrapolate. Returns `None` while its
  lookahead buffer is still filling.
- **`detection.py`** — `FaceDetector` (SCRFD) and `FaceEmbedder` (ArcFace), both ONNX. Owns all
  the geometry: SCRFD letterbox to 640×640 and rescale detections back to frame pixels; ArcFace
  5-point landmark affine-alignment to a 112×112 canonical crop; L2-normalized embeddings. Both
  are **batched**: `detect_batch` stacks N letterboxed frames into one SCRFD run; `embed_many`
  flattens every `(frame, detection)` pair across those frames into ArcFace chunks of ≤ M crops.
- **`tracking.py`** — `ByteTracker` wrapper (`trackers` + `supervision`). Uses buffered IoU
  (`BIoU`) because it's updated at detection cadence, not video fps. Uses ByteTrack only for stable
  ids; draws the matched *input* detection box, not the Kalman estimate.
- **`interpolation.py`** — `LookaheadTrackBuffer`: per-video-frame PCHIP/cubic/linear interpolation
  of tracked faces, matched across snapshots **by track id**. A render cursor trails the live frame
  by a fixed lookahead so every rendered frame lies strictly between two real detections.
- **`token_bucket.py`** — gate-then-spend throttle (`try_acquire` then `record_spend`) that adapts
  detection frequency to however long inference actually takes on this machine.
- **`metrics.py`** — `MetricsCollector`: trailing time-window (default 5s) moving averages of live
  pipeline stats (detections/frame, SCRFD vs ArcFace timings, detection-pass duration, detection
  rate, processed fps, active tracks). Recorded from `Engine.process` and read from the `/metrics`
  route — both on the event loop, so no locking. Surfaced in the browser by `static/metrics.js`,
  which polls `/metrics` twice a second into an overlay panel.
- **`overlay.py`** — OpenCV text overlay + dashed-rectangle helper (dashed = interpolated frame,
  solid = landed on a real detection).
- **`iso_bmff.py`** — minimal ISO BMFF box parser. Pipe reads never align to box boundaries, so
  `BoxReader.feed()` buffers until whole top-level boxes are available.
- **`broadcaster.py`** — single producer / many async consumers. Now on an **`asyncio.Condition`**
  (producer and consumers share one event loop — no thread bridging). Bounded `deque` gives
  drop-oldest retention; `wait_for_next()` raises `LaggedError` when a client falls off the back.
  New clients get `snapshot_for_new_client()`: init segment + only the single latest fragment.
- **`orchestrator.py`** — wires loader → engine → writer → box-parse → broadcaster via two
  long-lived asyncio tasks (`frame-forward`, `box-parse`), created with plain `create_task` (a
  `TaskGroup` held open across the context-manager yield would cancel whichever unrelated task
  entered the context when a child crashes). A crashed task logs, **records its exception as the
  failure reason** (`failure_reason`, read by the pipeline watchdog), and closes the broadcaster so
  failure is immediately client-visible. **End of stream**: source exhaustion closes the encoder's
  stdin (flushing trailing fragments); encoder EOF closes the broadcaster (no failure reason — a
  clean end).
- **`pipeline.py`** — `PipelineManager`: owns the pipeline's `AsyncExitStack` lifecycle. Starts
  **idle** (no pipeline; `app.state.broadcaster`/`engine`/`stream_id` all `None`). `start_url(url,
  loop)` / `start_synthetic(loop)` do a teardown-first build (serialized by a lock); `go_idle()`
  tears back down to idle. Each successful build updates `app.state.broadcaster`/`engine`/
  `stream_id` with a fresh random UUID (every build is a genuinely new broadcaster/encoder session
  — fresh init segment, PTS from zero — served at its own `/{stream_id}.mp4` URL). Every build also
  spawns a **watchdog task** (tagged with a monotonic generation number) that waits on the
  broadcaster closing; when it closes on its own (not superseded by an explicit stop/switch, which
  the generation check lets win) it takes the pipeline **to idle** — silently for a clean end, or
  recording an id-tagged `last_error` (from the orchestrator's `failure_reason`) that the player
  surfaces as a popup. `status()` reports `idle`/`playing`, the stream URL, and the last error.
- **`pipe_io.py`** — shared async subprocess-pipe helpers (`read_exact`, `drain_stderr`,
  `drain_and_discard`, `terminate_and_wait`); note the drain-during-shutdown requirement.
  `drain_stderr` logs decoder/encoder stderr at `INFO` (matching `app.py`'s log level) so ffmpeg
  failures are actually visible instead of silently swallowed at `DEBUG`.
- **`sample_asset.py`** — generates the synthetic `testsrc` sample on first run.
- **`app.py`** — Starlette wiring + `click` CLI (global tuning flags only; no source flags).
  `lifespan` builds the (idle) `PipelineManager`. Routes: `/` (player page), `/{stream_id}.mp4`
  (live tail for the given build's unique UUID; returns `410 Gone` when idle, when `stream_id`
  doesn't match the current build, or when its broadcaster is closed, so the player can distinguish
  "ended/idle" from a network error), `/api/status` (polled by the player: `idle`/`playing`, the
  stream URL, and the last failure), `/metrics` (`{}` while idle), `POST /api/source` (body is a
  `{url, loop}` or `{synthetic: true, loop}`; http/https-validated, 60 s timeout → `504`, other
  build failures → `400`), and `POST /api/stop` (go idle).
- **`static/player.js`** — browser: polls `/api/status` and gates on state. **Idle** → `showIdle()`
  blanks the `<video>` (`clearVideo()` detaches the media so it stops showing the last decoded frame
  and falls back to its black background) and hides the progress bar (no loading spinner while just
  waiting), shows "No source, waiting for URL", surfaces a new failure via `window.showSourceError`,
  and re-polls every 2 s. **Playing** → shows the loading bar, then `MediaSource` + `SourceBuffer`,
  streamed `fetch` of the status' stream URL, seek to the live edge (encoder PTS runs from server
  start, not client connect), trim old buffered ranges, `QuotaExceededError` recovery (requeue +
  evict older half). On error/`410`/stream end it blanks the frame and falls back to the idle poll.
- **`static/source.js`** — the source form: POSTs `{url, loop}` (or `{synthetic, loop}` for the Test
  pattern button) to `/api/source`, and Stop → `POST /api/stop`. Reports the result in the status
  line; on failure the full server error (yt-dlp/ffprobe stderr) is shown in a dismissible
  `#source-error` panel, since player.js's poll loop overwrites the shared status line. Exposes
  `window.showSourceError` so player.js reuses the same panel for async mid-stream failures. The
  actual stream handoff rides on player.js's poll loop.

## Working in this codebase

- The whole pipeline is a **singleton per process** (owned by `PipelineManager`; starts idle, built
  on `/api/source`, torn down on end/failure/`/api/stop`), not one per client — all clients share
  one decode/CV/encode chain and differ only in which fragments they've consumed. While idle there
  is no pipeline at all, so routes must tolerate `app.state.broadcaster`/`engine` being `None`.
- **Detection is decoupled from video frame rate.** It runs off-thread at whatever rate the
  `TokenBucket` allows; `interpolation.py` fills every intermediate frame. If you touch detection
  cost, tracking cadence, or `lookahead`, keep the delay buffer (`pending_frames` in `engine.py`)
  consistent with the interpolation cursor or overlays will trail/lead the faces.
- Detection always runs on the **pristine** frame; overlays are drawn on a *copy* of a delayed
  frame. Never draw before detecting or the detector sees burned-in text.
- Encoder settings (`_encoder_cmd` in `writer.py`) are load-bearing for MSE compatibility: baseline
  profile, self-contained fragments, and a GOP aligned to `KEYFRAME_INTERVAL_SECONDS` so fragment
  boundaries land on keyframes. The browser MIME in `player.js` (`avc1.42001e`) must match.
