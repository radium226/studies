# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this
subproject. See the repo root `CLAUDE.md` for how this fits alongside `app/` and `kernel/`.

## What this is

`video-analyzer-core` (importable as `video_analyzer.core`) — concrete, real-backend
implementations of [`kernel`](../kernel)'s service ABCs: SCRFD face detection, ArcFace face
embedding, ByteTrack multi-object tracking, PCHIP/cubic/linear spline interpolation,
histogram-correlation scene-cut detection, and ffmpeg-backed frame I/O. Most of it was ported
from `app/src/video_streamer/*` and adapted to satisfy `kernel`'s (async) contracts and richer
`Detection`/`Face`/`TrackedFace` models.

`kernel` stays dependency-free by design (see its own `CLAUDE.md`) — every numpy/scipy/opencv/
onnxruntime-touching algorithm lives here instead, even when the algorithm itself is pure math
(e.g. SCRFD box/landmark decoding, NMS, ByteTrack IoU re-association, spline dispatch).

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
│                           via hand-rolled IoU (_best_iou_match, each face claimed at most once)
│                           so drawn boxes/landmarks stay consistent — cheap bookkeeping, no
│                           executor offload needed. Implements reset() (scene cuts) by
│                           delegating to the vendored tracker's own reset; exposes the ByteTrack
│                           knobs as ctor kwargs. NB: `fps` is bookkeeping only — lost_track_buffer
│                           counts *updates* (detection passes), not video frames
├── spline_interpolator.py  SplineInterpolator(kernel.Interpolator) — pchip/cubic/linear *point
│                           query* via scipy (evaluate one position of the window, per the
│                           kernel contract), generic over anything implementing
│                           kernel.Interpolable (to_vector/with_vector) — knows nothing about
│                           faces or tracks
├── histogram_scene_detector.py HistogramSceneDetector(kernel.SceneDetector) — per-channel
│                           histogram correlation of consecutive frames; a cut is a correlation
│                           below the ctor threshold. Cheap enough to run inline per frame pair
├── ffmpeg_frame_source.py  FfmpegFrameSource(kernel.FrameSource) — spawns the decoder ffmpeg
│                           subprocess, probes video info, reshapes raw BGR24 bytes to
│                           (H, W, 3) numpy frames
├── ffmpeg_frame_sink.py    FfmpegFrameSink(kernel.FrameSink) — spawns the encoder ffmpeg
│                           subprocess, writes raw BGR24 bytes to its stdin. The encoder's
│                           *output* side (fMP4 fragments, box parsing, broadcasting to
│                           viewers) is NOT part of any kernel contract — read
│                           read_output_chunk() yourself and wire it up downstream, same as
│                           app/orchestrator.py does today
├── overlay.py               draw_caption_text / draw_dashed_rect — pure cv2 helpers, no ABC to
│                           satisfy; draw onto annotated_frame.frame.content before it reaches
│                           a FrameSink if you want boxes burned into the video
├── stop_after_frame_count.py StopAfterFrameCount(kernel.FrameSource) — decorates a FrameSource,
│                           requests an early kernel.StopToken stop once the Nth frame has been
│                           read (still returns that frame). Generic, no numpy — lives here only
│                           because kernel exposes the StopToken primitive but no concrete
│                           condition for what should set it
├── stop_on_first_track.py  StopOnFirstTrack(kernel.FrameBroadcaster) — decorates a
│                           FrameBroadcaster, requests an early kernel.StopToken stop once one
│                           same track id has appeared in `min_track_frames` *rendered* frames
│                           (default 1). The definition of a track in video frames — exact,
│                           interpolated, and held appearances all count, which is why it sits
│                           on the output side: interpolated frames only exist downstream of
│                           the render cursor. Per-track counts reset at scene-start frames
│                           (ByteTrack may reuse ids after its own reset). Also generic
├── yt_dlp_url_resolver.py  resolve_direct_media_url — resolves a page URL (YouTube, etc.) to a
│                           direct media URL via the `yt-dlp` CLI (a subprocess on PATH, like
│                           ffmpeg/ffprobe — not a Python package), so it can be handed to
│                           FfmpegFrameSource like any other source string. Ported from
│                           app/input_video.py's _resolve_direct_media_url
├── pipe_io.py              **public** asyncio subprocess-pipe helpers, shared by the two ffmpeg
    wrappers and reused downstream (cli's FfplayFrameSink): read_exact, drain_stderr,
    drain_and_discard, terminate_and_wait, shutdown_process — mind the drain-during-shutdown
    requirement documented inline, or Process.wait() hangs
```

`FrameBroadcaster` has no concrete implementation here — it's meant to be supplied by whatever
application wires the pipeline together (e.g. push `AnnotatedFrame` metadata over a websocket); `core` has no opinion
on transport.

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
  than making it a hard requirement to run `pytest`. `resolve_direct_media_url` *is* tested
  (`tests/test_yt_dlp_url_resolver.py`) despite spawning yt-dlp in production — the tests swap
  `asyncio.create_subprocess_exec` for a fake, so no binary or network is ever needed.
- `OnnxFaceDetector`/`OnnxFaceEmbedder` offload `session.run()` via `run_in_executor` (an
  injectable `Executor`, defaulting to the loop's default thread pool) — `ByteTrackTracker.update`
  deliberately does not, since ByteTrack's update is cheap bookkeeping, not inference; don't add
  executor offloading there without a reason.
- `StopAfterFrameCount`/`StopOnFirstTrack` are the only generic classes in `core` — an
  intentional exception to "everything that computes numbers belongs here, only `kernel`'s
  `Interpolator`/`Interpolable` stays generic": neither touches frame content at all (one counts
  reads, the other counts rendered frames per track id), so pinning them to `NDArray[np.uint8]`
  like every other backend here would just be dishonest about what they depend on.
- `app/` was not touched when this project was created and still has its own inline
  `engine.py`/`detection.py`/`tracking.py`/`interpolation.py`/`reader.py`/`writer.py` copies —
  migrating `app/` to depend on `core`+`kernel` instead is a separate, not-yet-done task.
