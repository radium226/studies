# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this
subproject. See the repo root `CLAUDE.md` for how this fits alongside `app/` and `kernel/`.

## What this is

`video-analyzer-core` (importable as `video_analyzer.core`) — concrete, real-backend
implementations of [`kernel`](../kernel)'s service ABCs: SCRFD face detection, ArcFace face
embedding, ByteTrack multi-object tracking, PCHIP/cubic/linear spline interpolation, and
ffmpeg-backed frame I/O. Everything here was ported from `app/src/video_streamer/*` and adapted
to satisfy `kernel`'s (async) contracts and richer `Detection`/`Face`/`TrackedFace` models.

`kernel` stays dependency-free by design (see its own `CLAUDE.md`) — every numpy/scipy/opencv/
onnxruntime-touching algorithm lives here instead, even when the algorithm itself is pure math
(e.g. SCRFD box/landmark decoding, NMS, ByteTrack IoU re-association, PCHIP dispatch).

Depends on `kernel` via a `uv` path source (`../kernel`, editable) — not a workspace, matching
the standalone-project style `app/` and `kernel/` already use.

## Commands

All commands run from this directory (a `uv`-managed Python project).

```bash
uv sync                        # install/update dependencies from uv.lock (resolves the
                                # ../kernel path dependency too)
uv run pytest                  # unit tests — pure-Python/numpy only, no ffmpeg/ONNX process
uv run ruff check src tests    # lint
uv run ty check src tests      # type check
```

## Architecture

```
src/video_analyzer/core/
├── _onnx_model.py          OnnxModel — shared ONNX session lifecycle (lazy load in
│                           __enter__, released in __exit__); base for both ONNX classes below
├── onnx_face_detector.py   OnnxFaceDetector(kernel.FaceDetector) — SCRFD: letterbox
│                           preprocessing, multi-stride decode, NMS, all pure numpy/cv2;
│                           only session.run() is offloaded via run_in_executor
├── onnx_face_embedder.py   OnnxFaceEmbedder(kernel.FaceEmbedder) — ArcFace: 5-point landmark
│                           affine alignment to a 112x112 canonical crop, batched embedding
├── bytetrack_tracker.py    ByteTrackTracker(kernel.Tracker) — wraps trackers.ByteTrackTracker
│                           (buffered IoU, since detection cadence << video fps); re-associates
│                           ByteTrack's Kalman-smoothed box back to the matched input detection
│                           via hand-rolled IoU (_best_match) so drawn boxes/landmarks stay
│                           consistent — cheap bookkeeping, no executor offload needed
├── pchip_interpolator.py   PchipInterpolator(kernel.Interpolator) — the pure numeric half of
│                           the pre-kernel LookaheadTrackBuffer: pchip/cubic/linear dispatch via
│                           scipy, generic over anything implementing kernel.Interpolable
│                           (to_vector/from_vector) — knows nothing about faces or tracks
├── ffmpeg_frame_source.py  FfmpegFrameSource(kernel.FrameSource) — spawns the decoder ffmpeg
│                           subprocess, probes video info, reshapes raw BGR24 bytes to
│                           (H, W, 3) numpy frames
├── ffmpeg_frame_sink.py    FfmpegFrameSink(kernel.FrameSink) — spawns the encoder ffmpeg
│                           subprocess, writes raw BGR24 bytes to its stdin. The encoder's
│                           *output* side (fMP4 fragments, box parsing, broadcasting to
│                           viewers) is NOT part of any kernel contract — read
│                           read_output_chunk() yourself and wire it up downstream, same as
│                           app/orchestrator.py does today
├── overlay.py               draw_overlay / draw_dashed_rect — pure cv2 helpers, no ABC to
│                           satisfy; draw onto annotated_frame.frame.content before it reaches
│                           a FrameSink if you want boxes burned into the video
├── stop_after_frame_count.py StopAfterFrameCount(kernel.FrameSource) — decorates a FrameSource,
│                           requests an early kernel.StopToken stop once the Nth frame has been
│                           read (still returns that frame). Generic, no numpy — lives here only
│                           because kernel exposes the StopToken primitive but no concrete
│                           condition for what should set it
├── stop_on_face_found.py   StopOnFaceFound(kernel.FrameBroadcaster) — decorates a
│                           FrameBroadcaster, requests an early kernel.StopToken stop the first
│                           time an AnnotatedFrame carries a detection. Also generic
├── _pipe_io.py             shared asyncio subprocess-pipe helpers for the two ffmpeg wrappers
    (read_exact, drain_stderr, drain_and_discard, shutdown_process — mind the drain-during-
    shutdown requirement documented inline, or Process.wait() hangs)
```

Not implemented here (no source algorithm exists anywhere to port, so nothing was invented):
`SceneDetector`. `FrameBroadcaster` has no concrete implementation either — it's meant to be
supplied by whatever application wires the pipeline together (e.g. push `AnnotatedFrame`
metadata over a websocket); `core` has no opinion on transport.

ONNX model weights are **not** bundled — `OnnxFaceDetector`/`OnnxFaceEmbedder` take a
`model_path: Path` constructor argument; point it at `app/models/scrfd_10g_kps_dynamic.onnx` /
`app/models/arcface_w600k_r50_batch.onnx` (or your own weights) when wiring up a real pipeline.

## Working in this codebase

- This is where new algorithm **backends** go — a different detector model, a different
  tracker, a GPU execution provider, etc. — as long as they implement one of `kernel`'s ABCs.
  New *contracts* (a new kind of service, a new data shape) belong in `kernel` instead.
  Only `kernel`'s `Interpolator`/`Interpolable` protocol should stay generic; everything that
  actually computes numbers belongs here.
- Testing boundary matches `app/`'s own convention (see the repo root `CLAUDE.md`): tests are
  pure-Python/numpy units with no ffmpeg subprocess or ONNX model file required.
  `ByteTrackTracker`/`trackers`/`supervision` are lightweight enough to exercise directly (see
  `tests/test_bytetrack_tracker.py`); `OnnxFaceDetector`/`OnnxFaceEmbedder`/
  `FfmpegFrameSource`/`FfmpegFrameSink` have no unit tests for the same reason `app/detection.py`,
  `engine.py`, `reader.py`, `writer.py` don't — they need real model weights / an `ffmpeg` binary.
  If you add coverage for those, gate it behind the weights/binary actually being present rather
  than making it a hard requirement to run `pytest`.
- `OnnxFaceDetector`/`OnnxFaceEmbedder` offload `session.run()` via `run_in_executor` (an
  injectable `Executor`, defaulting to the loop's default thread pool) — `ByteTrackTracker.update`
  deliberately does not, since ByteTrack's update is cheap bookkeeping, not inference; don't add
  executor offloading there without a reason.
- `StopAfterFrameCount`/`StopOnFaceFound` are the only generic classes in `core` — an intentional
  exception to "everything that computes numbers belongs here, only `kernel`'s `Interpolator`/
  `Interpolable` stays generic": neither touches frame content at all (one counts reads, the other
  inspects `detections`), so pinning them to `NDArray[np.uint8]` like every other backend here
  would just be dishonest about what they depend on.
- `app/` was not touched when this project was created and still has its own inline
  `engine.py`/`detection.py`/`tracking.py`/`interpolation.py`/`reader.py`/`writer.py` copies —
  migrating `app/` to depend on `core`+`kernel` instead is a separate, not-yet-done task.
