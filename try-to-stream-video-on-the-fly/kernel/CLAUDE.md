# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this
subproject. See the repo root `CLAUDE.md` for how this fits alongside `app/` and `core/`.

## What this is

`video-analyzer-kernel` (importable as `video_analyzer.kernel`) — the dependency-free contract
layer of the video-analyzer CV pipeline: service ABCs, generic data shapes, and orchestration
*timing* (when to batch, when to advance the render cursor). It defines what a face detector,
embedder, tracker, interpolator, and frame source/sink look like, and wires them together in a
`Pipeline`, but contains no algorithm implementation itself — no numpy, scipy, opencv, or
onnxruntime. Those live in the sibling `core/` project, which implements this package's ABCs
with real SCRFD/ArcFace/ByteTrack/PCHIP/ffmpeg backends.

Only runtime dependencies: `loguru`, `pyyaml`. Keep it that way — if a change needs numpy or
similar to do real math, that code belongs in `core/`, not here.

## Commands

All commands run from this directory (a `uv`-managed Python project).

```bash
uv sync                        # install/update dependencies from uv.lock
uv run pytest                  # unit tests — all pure-Python, no ffmpeg/ONNX needed
uv run ruff check src tests    # lint
uv run ty check src tests      # type check
```

## Architecture

```
src/video_analyzer/kernel/
├── models/            # generic, frozen dataclasses — pure data, no behavior beyond
│                       # Interpolable's to_vector/from_vector on TrackedFace
│   ├── frame.py            Frame[FrameContentT] (index + content), FrameIndex = int
│   ├── bounding_box.py      BoundingBox (x, y, width, height — floats, sub-pixel)
│   ├── detection.py         Detection (bounding_box + 5 landmarks + confidence)
│   ├── face.py              Face[FaceEmbeddingT] (detection + embedding)
│   ├── tracked_face.py      TrackedFace[FaceEmbeddingT] (track_id + face); implements
│   │                        Interpolable by flattening bbox+landmarks to 14 floats
│   ├── snapshot.py          Snapshot[DetectionT] (frame_index + list of detections)
│   └── annotated_frame.py   AnnotatedFrame (frame + detections + bracket + is_exact + flushed)
├── services/           # ABCs only — every method is `async def` (except Clock.now) so
│                       # real implementations can offload blocking I/O/inference
│   ├── clock.py             Clock: now() -> float
│   ├── frame_source.py      FrameSource[T]: read_frame() -> Frame[T] | None
│   ├── frame_sink.py        FrameSink[T, D]: write_frame(AnnotatedFrame[T, D])
│   ├── frame_broadcaster.py FrameBroadcaster[T, D]: broadcast_frame(AnnotatedFrame[T, D])
│   ├── face_detector.py     FaceDetector[T]: detect_faces(frame_batch) -> list[list[Face[None]]]
│   ├── face_embedder.py     FaceEmbedder[T, E]: embed_faces(list[(Frame[T], Face[None])])
│   │                        -> list[Face[E]] — takes the frame too, since alignment needs pixels
│   ├── tracker.py           Tracker[E]: update(faces) -> list[TrackedFace[E]]
│   ├── interpolator.py      Interpolable protocol (to_vector/from_vector) + Interpolator[T]:
│   │                        interpolate(list[T | None]) -> list[T] — stateless gap-fill
│   └── scene_detector.py    SceneDetector[T]: detect_scene_cut(prev, curr) -> bool (not yet
│                            wired into Pipeline — no scene-cut algorithm exists to drive it)
├── token_bucket.py     TokenBucket — continuous-refill rate limiter (gate-then-spend)
├── batch_gate.py       BatchGate — fires a detection batch when full or lag exceeded, built
│                       on TokenBucket
├── config.py           PipelineConfig/BatchingConfig/RenderingConfig — YAML-backed, strict
│                       (unknown keys are hard errors)
└── pipeline.py         Pipeline[FrameContentT, FaceEmbeddingT] — the orchestrator
```

### `Pipeline.drain()` — six concurrent stages over `Channel`s

1. **`produce_frames`** — reads from `FrameSource`, fans each frame out to two channels: one
   for detection sampling, one for eventual rendering (every frame gets rendered, not just
   sampled ones).
2. **`sample_and_detect`** — buffers frames, uses `BatchGate.should_fire()` to decide when to
   fire (batch full or lag exceeded), samples up to `config.batching.max_frames` frames evenly
   spread since the last fire (plain int/float math — no numpy), calls `FaceDetector.detect_faces`
   once per fire. Because detection is awaited *inline*, each iteration first drains everything
   already queued (`Channel.try_recv`) so the sample really does span the whole interval since the
   last pass. Consuming one frame per iteration instead would walk the backlog oldest-first and
   fall permanently further behind the source — one frame per pass, no batching, detections always
   stale.
3. **`embed_faces`** — flattens every sampled frame's faces in a batch into one
   `FaceEmbedder.embed_faces` call (real cross-frame batching), re-splits per frame.
4. **`track`** — calls `Tracker.update` once per frame **in order** (tracking is stateful/temporal,
   frames within a batch cannot be reordered or parallelized).
5. **`interpolate_and_render`** — `_RenderCursor` (private to `pipeline.py`) lags
   `config.rendering.lookahead_snapshots` snapshots behind the newest tracked snapshot so every
   emitted frame lies strictly between two real detections (never extrapolated); the actual
   numeric fill is delegated to the injected `Interpolator` — this class only owns segment/window/
   prune/cap timing, ported from the pre-kernel `LookaheadTrackBuffer`.
6. **`render_and_sink`** — hands the resulting `AnnotatedFrame` to both `FrameSink` (video bytes)
   and `FrameBroadcaster` (detection metadata, for other consumers). The kernel doesn't draw: if you
   want overlays burned in, the sink does it — onto a **copy**, since both consumers get the same
   `Frame` object and a `FrameSource` may hand back read-only content (the ffmpeg one does).

Every stage closes its output channel in a `finally`, and `interpolate_and_render` **cancels** its
snapshot collector rather than awaiting it. Both matter for failure, not the happy path: a stage
that dies or gets cancelled must still deliver an end of stream, or a consumer parked on an open
channel wedges the `TaskGroup` shutdown and the original exception is never reported — the process
just hangs. `tests/test_pipeline.py` pins this.

The two channels carrying whole frames (`render_frames`, `annotated_frames`) are **bounded**, so a
sink slower than the source pushes backpressure back to the decoder. Unbounded, a slow sink silently
banks gigabytes of raw frames and then looks like a hang at end of stream while the backlog drains.
For the same reason both `pending`/`pending_frames` buffers prune everything the cursor has passed
instead of waiting for the `_MAX_PENDING_FRAMES` cap.

## Working in this codebase

- **Dependency-free is the whole point.** Before adding an import, ask whether it's a genuine new
  *contract* or *data shape* — those belong here. Algorithm code that needs numpy/scipy/opencv/
  onnxruntime to run belongs in `core/`, even if the algorithm itself is pure/deterministic (e.g.
  SCRFD decoding, NMS, PCHIP dispatch, ByteTrack IoU matching all live in `core/`, not here).
- Every service ABC method is `async def` on purpose (except `Clock.now`) so a real
  ffmpeg/ONNX-backed implementation can `await loop.run_in_executor(...)` internally without
  blocking the shared event loop — don't make a new service ABC method sync.
- `FaceEmbedder.embed_faces` takes `(Frame, Face)` pairs, not bare `Face` — this was a real bug
  caught mid-implementation: alignment needs the source frame's pixels, not just geometry.
- `Interpolator.interpolate` is intentionally stateless (`list[T | None] -> list[T]`, filling gaps
  at fixed integer positions) — all the *timing* state (which snapshots bracket the query point,
  when to prune) lives in `Pipeline`'s `_RenderCursor`, not in the interpolator.
- Tests use hand-written fakes under `tests/fake/` (mirrors `app/`'s testing style — no mocking
  framework). `tests/fake/services.py`'s fake `FrameSource.read_frame` has a deliberate
  `await asyncio.sleep(0)` — without it, produce_frames races through every frame in one
  scheduler turn and starves the other stages, since none of the fakes have a real suspension
  point the way ffmpeg/ONNX I/O would.
