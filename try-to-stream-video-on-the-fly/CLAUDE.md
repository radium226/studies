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
```

Useful CLI flags (see `app.py:main`):

- `--input-video {synthetic,url}` — synthetic `testsrc` asset (default) or a `--input-video-url`
  resolved through `yt-dlp`.
- `--repeat-input-video` — loop the source forever (`ffmpeg -stream_loop -1`).
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

The app listens on `http://127.0.0.1:8000`. There is no test suite, linter, or formatter wired
into a CI step, though `ruff` and `ty` are available as dev dependencies.

### System dependencies

These are invoked as subprocesses (not Python packages) and must be on `PATH`:

- **`ffmpeg` / `ffprobe`** — decode, encode, probe, and generate the synthetic sample asset.
- **`yt-dlp`** — only for `--input-video=url`; resolves the page URL to a direct media URL.

ONNX model weights live in `app/models/` (`scrfd_10g_kps_dynamic.onnx`,
`arcface_w600k_r50_batch.onnx`) and are loaded by `detection.py` via `onnxruntime`
(CPU execution provider).

## Architecture

The process is **fully asyncio** end to end. Two `ffmpeg` subprocesses (decode, encode) bracket
an in-process CV `Engine`; the only place work leaves the event loop is ONNX inference, offloaded
to a single-worker `ThreadPoolExecutor`. Everything is composed as async context managers
(`.start()` classmethods) in `app.py`'s `lifespan`, so one shared pipeline serves all clients.

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
  long-lived asyncio tasks (`frame-forward`, `box-parse`).
- **`pipe_io.py`** — shared async subprocess-pipe helpers (`read_exact`, `drain_stderr`,
  `drain_and_discard`, `terminate_and_wait`); note the drain-during-shutdown requirement.
- **`sample_asset.py`** — generates the synthetic `testsrc` sample on first run.
- **`app.py`** — Starlette wiring + `click` CLI. `lifespan` builds the whole pipeline once.
- **`static/player.js`** — browser: `MediaSource` + `SourceBuffer`, streamed `fetch`, seek to the
  live edge (encoder PTS runs from server start, not client connect), trim old buffered ranges.

## Working in this codebase

- The whole pipeline is a **singleton per process** (built in `lifespan`), not one per client —
  all clients share one decode/CV/encode chain and differ only in which fragments they've consumed.
- **Detection is decoupled from video frame rate.** It runs off-thread at whatever rate the
  `TokenBucket` allows; `interpolation.py` fills every intermediate frame. If you touch detection
  cost, tracking cadence, or `lookahead`, keep the delay buffer (`pending_frames` in `engine.py`)
  consistent with the interpolation cursor or overlays will trail/lead the faces.
- Detection always runs on the **pristine** frame; overlays are drawn on a *copy* of a delayed
  frame. Never draw before detecting or the detector sees burned-in text.
- Encoder settings (`_encoder_cmd` in `writer.py`) are load-bearing for MSE compatibility: baseline
  profile, self-contained fragments, and a GOP aligned to `KEYFRAME_INTERVAL_SECONDS` so fragment
  boundaries land on keyframes. The browser MIME in `player.js` (`avc1.42001e`) must match.
