# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this
subproject. See the repo root `CLAUDE.md` for how this fits alongside `app/` and `core/`.

## What this is

`video-analyzer-kernel` (importable as `video_analyzer.kernel`) — the dependency-free contract
layer of the video-analyzer CV pipeline: service ABCs, generic data shapes, and orchestration
*timing* (when to batch, when to advance the render cursor). It defines what a face detector,
embedder, tracker, interpolator, scene detector, and frame source/sink look like, and wires them
together in a `Pipeline`, but contains no algorithm implementation itself — no numpy, scipy,
opencv, or onnxruntime. Those live in the sibling `core/` project, which implements this
package's ABCs with real SCRFD/ArcFace/ByteTrack/spline/histogram/ffmpeg backends.

Only runtime dependencies: `loguru`, `pyyaml`. Keep it that way — if a change needs numpy or
similar to do real math, that code belongs in `core/`, not here.

Each service ABC carries its contract in its **docstring** (ordering guarantees, statefulness,
monotonicity, copy-before-mutate rules) — that's the authoritative reference for implementers,
not this file.

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
│                       # Interpolable's to_vector/with_vector on TrackedFace
│   ├── frame.py            Frame[FrameContentT] (index + content + is_scene_start),
│   │                        FrameIndex = int
│   ├── bounding_box.py      BoundingBox (x, y, width, height — floats, sub-pixel)
│   ├── detection.py         Detection (bounding_box + landmarks + confidence);
│   │                        FaceLandmarks (left_eye, right_eye, nose, mouth_left, mouth_right —
│   │                        the standard SCRFD/ArcFace 5-point order)
│   ├── face.py              Face[FaceEmbeddingT] (detection + embedding)
│   ├── tracked_face.py      TrackedFace[FaceEmbeddingT] (track_id + face); implements
│   │                        Interpolable by flattening bbox+landmarks to 14 floats
│   ├── snapshot.py          Snapshot[FaceRecordT] (frame_index + faces + is_scene_start,
│   │                        carried over from the sampled frame)
│   └── annotated_frame.py   AnnotatedFrame (frame + faces + interpolation_bracket + is_exact);
│                            bracket is None for *held* frames (no bracketing detections —
│                            the flushed tail, inter-scene gaps)
├── services/           # ABCs only — every method is `async def` (except Clock.now) so
│                       # real implementations can offload blocking I/O/inference;
│                       # contracts live in the docstrings
│   ├── clock.py             Clock: now() -> float (monotonic seconds)
│   ├── frame_source.py      FrameSource[FrameContentT]: read_frame() -> Frame[FrameContentT] | None
│   ├── frame_sink.py        FrameSink[FrameContentT, FaceRecordT]:
│   │                        write_frame(AnnotatedFrame[FrameContentT, FaceRecordT])
│   ├── frame_broadcaster.py FrameBroadcaster[FrameContentT, FaceRecordT]: the metadata twin of
│   │                        FrameSink — same AnnotatedFrame, for non-video consumers
│   ├── face_detector.py     FaceDetector[FrameContentT]:
│   │                        detect_faces(frame_batch) -> list[list[Face[None]]] (one per frame,
│   │                        in order)
│   ├── face_embedder.py     FaceEmbedder[FrameContentT, FaceEmbeddingT]:
│   │                        embed_faces(list[(Frame[FrameContentT], Face[None])])
│   │                        -> list[Face[FaceEmbeddingT]] — takes the frame too, since alignment
│   │                        needs pixels
│   ├── tracker.py           Tracker[FaceEmbeddingT]: update(faces) -> list[TrackedFace[...]],
│   │                        stateful and order-dependent; reset() forgets all temporal state
│   │                        (called on scene cuts)
│   ├── interpolator.py      Interpolable protocol (to_vector/with_vector) +
│   │                        Interpolator[InterpolableT]: interpolate(list[InterpolableT | None],
│   │                        at) -> InterpolableT — stateless *point query* (evaluate one
│   │                        position, not fill the whole window)
│   └── scene_detector.py    SceneDetector[FrameContentT]: detect_scene_cut(prev, curr) -> bool —
│                            called by produce_frames on every consecutive pair; True marks the
│                            current frame as a scene start
├── stop_token.py       StopToken — cooperative one-shot signal that ends `produce_frames`'
│                       loop early; kernel exposes only this primitive, no concrete stop
│                       condition (see below)
├── token_bucket.py     TokenBucket — continuous-refill rate limiter; gate-then-spend protocol
│                       (`can_spend` checks the budget without deducting, `record_spend` charges
│                       the actual cost afterwards)
├── batch_gate.py       BatchGate — fires a detection batch when full or lag exceeded, built
│                       on TokenBucket; `max_frames`/`max_lag_ms` match `BatchingConfig` 1:1.
│                       Note: the `max_lag_ms` epoch resets whenever the bucket is empty
├── render_cursor.py    RenderCursor — sequential no-duplicate walk of frame indices through the
│                       interpolation window (see stage 5 below); scene-aware; finish() flushes
│                       the end-of-stream tail. Internal (not exported), but directly unit-tested
├── config.py           PipelineConfig/BatchingConfig/RenderingConfig — YAML-backed, strict
│                       (unknown keys are hard errors); defaults live only on the dataclass
│                       fields — from_dict passes just the keys present
└── pipeline.py         Pipeline[FrameContentT, FaceEmbeddingT] — the orchestrator; injected
                        services are private attributes; one `run()` per instance (stateful
                        services would silently carry over otherwise)
```

### `Pipeline.run()` — six concurrent stages over `Channel`s

1. **`produce_frames`** — reads from `FrameSource`, runs `SceneDetector.detect_scene_cut` on
   every consecutive frame pair (a cut marks the current frame `is_scene_start`), then fans each
   frame out to two channels: one for detection sampling, one for eventual rendering (every
   frame gets rendered, not just sampled ones). Before each read it also checks an injected
   `StopToken`; once set, it takes the exact same exit path as source exhaustion — no separate
   teardown logic exists anywhere else in the pipeline for an early stop.
2. **`sample_and_detect`** — buffers frames, uses `BatchGate.should_fire()` to decide when to
   fire (batch full or lag exceeded), samples up to `config.batching.max_frames` frames evenly
   spread since the last fire (plain int/float math — no numpy), calls `FaceDetector.detect_faces`
   once per fire. Because detection is awaited *inline*, each iteration first drains everything
   already queued (`Channel.try_recv`) so the sample really does span the whole interval since the
   last pass. A scene-start frame evicts the still-unsampled pre-cut frames from the buffer, so no
   detection batch ever spans a cut.
3. **`embed_faces`** — flattens every sampled frame's faces in a batch into one
   `FaceEmbedder.embed_faces` call (real cross-frame batching), re-splits per frame into
   `Snapshot`s (carrying the frame's `is_scene_start` along).
4. **`track_faces`** — calls `Tracker.update` once per frame **in order** (tracking is
   stateful/temporal, frames within a batch cannot be reordered or parallelized); calls
   `Tracker.reset()` first on a scene-start snapshot — identities never survive a cut.
5. **`interpolate_and_render`** — `RenderCursor` (in `render_cursor.py`) lags
   `config.rendering.lookahead_snapshots` snapshots behind the newest tracked snapshot so every
   emitted frame lies strictly between two real detections (never extrapolated); the actual
   numeric fill is delegated to the injected `Interpolator` (a point query). The cursor walks
   frame indices **sequentially, no duplicates, no gaps**: the stage loops `advance()` until it
   returns None — nothing while detections stall, a catch-up burst once they land. Snapshots are
   partitioned by scene; interpolation never bridges a cut (the gap between a scene's last
   snapshot and the cut is emitted *held* at its final positions). On clean end of stream the
   stage awaits the trailing snapshots, calls `cursor.finish()` (drops the lookahead margin), and
   flushes every remaining pending frame — the tail past the last snapshot goes out held
   (`interpolation_bracket=None`). **Net guarantee: every input frame is emitted exactly once**
   (short of the `_MAX_PENDING_FRAMES` eviction under an extreme stall).
6. **`write_and_broadcast`** — hands the resulting `AnnotatedFrame` to both `FrameSink` (video
   bytes) and `FrameBroadcaster` (detection metadata, for other consumers). The kernel doesn't
   draw: if you want overlays burned in, the sink does it — onto a **copy**, since both consumers
   get the same `Frame` object and a `FrameSource` may hand back read-only content (the ffmpeg one
   does).

Every stage closes its output channel in a `finally` (`Channel.close()` is sync and idempotent;
consumers still drain what was queued before the close), and `interpolate_and_render` **cancels**
its snapshot collector in its `finally` rather than awaiting it (it does await it on the *clean*
path, before the end-of-stream flush — upstream finallys guarantee the collector then finishes).
Both matter for failure, not the happy path: a stage that dies or gets cancelled must still
deliver an end of stream, or a consumer parked on an open channel wedges the `TaskGroup` shutdown
and the original exception is never reported — the process just hangs. `tests/test_pipeline.py`
pins this.

`Pipeline.run()` accepts an optional `StopToken`. Setting it only stops `produce_frames` from
reading further frames — it never cancels or hard-cuts anything downstream; frames already read
keep flowing through detection/tracking/interpolation/render exactly as they would at natural end
of stream, so an early, graceful stop needs no teardown path beyond the one that already exists
for source exhaustion. Kernel exposes only the token: deciding *when* to call `request_stop()`
(after N frames, once a track is confirmed, ...) is composing code's job, built as a decorator
around an existing service (`FrameSource`, `Tracker`, `FrameBroadcaster`, `FrameSink`, ...) — not
a new kernel service ABC.

**Every** channel carrying whole frames is bounded, so a stage slower than the source pushes
backpressure back to the decoder. `render_frames` and `annotated_frames` get
`_FRAME_CHANNEL_CAPACITY`; `detection_frames` gets that plus headroom derived from the batching
knobs (`_detection_channel_capacity`) since frames legitimately queue there while a detection
pass is awaited inline — only a genuinely stuck detector should ever hit its bound. Unbounded, a
slow sink silently banks gigabytes of raw frames and then looks like a hang at end of stream
while the backlog drains. For the same reason every `pending_frames` buffer (one in
`sample_and_detect`, one in `interpolate_and_render`) prunes everything the cursor has passed
instead of waiting for the `_MAX_PENDING_FRAMES` cap.

## Working in this codebase

- **Dependency-free is the whole point.** Before adding an import, ask whether it's a genuine new
  *contract* or *data shape* — those belong here. Algorithm code that needs numpy/scipy/opencv/
  onnxruntime to run belongs in `core/`, even if the algorithm itself is pure/deterministic (e.g.
  SCRFD decoding, NMS, spline dispatch, ByteTrack IoU matching, histogram scene-cut scoring all
  live in `core/`, not here).
- Every service ABC method is `async def` on purpose (except `Clock.now`) so a real
  ffmpeg/ONNX-backed implementation can `await loop.run_in_executor(...)` internally without
  blocking the shared event loop — don't make a new service ABC method sync. Put the contract in
  the method's docstring, not (only) here.
- `FaceEmbedder.embed_faces` takes `(Frame, Face)` pairs, not bare `Face` — this was a real bug
  caught mid-implementation: alignment needs the source frame's pixels, not just geometry.
- `Interpolator.interpolate` is intentionally stateless and a **point query**
  (`(list[InterpolableT | None], at) -> InterpolableT`) — all the *timing* state (which snapshots
  bracket the query point, when to prune) lives in `RenderCursor`, not in the interpolator. It
  used to fill the whole window per call; the pipeline only ever needs one position per frame.
- **Don't reintroduce frame drops.** `RenderCursor.advance()`'s sequential no-duplicate walk plus
  the end-of-stream flush is what guarantees N frames in ⇒ N frames out; the pre-cursor design
  (one advance per incoming frame, duplicates silently skipped) dropped frames whenever detection
  lagged and abandoned the lookahead tail at EOS. `tests/test_render_cursor.py` and the
  exact-count assertions in `tests/test_pipeline.py` pin the invariant.
- `TrackedFace.with_vector` (part of the `Interpolable` protocol) is an **instance** method, not a
  factory/classmethod, even though the name pattern might suggest one: it reuses `self`'s
  `track_id`/`embedding`/`confidence` as a template and only replaces the interpolated geometry.
  Callers always need an existing instance to call it on.
- The generic `FaceRecordT` (on `Snapshot`, `AnnotatedFrame`, `FrameSink`, `FrameBroadcaster`) is
  never actually bound to the concrete `Detection` model — it's `Face[FaceEmbeddingT]` upstream of
  tracking and `TrackedFace[FaceEmbeddingT]` downstream of it. Don't confuse it with `Detection`.
- Tests use hand-written fakes under `tests/fake/` (mirrors `app/`'s testing style — no mocking
  framework). `tests/fake/services.py`'s fake `FrameSource.read_frame` has a deliberate
  `await asyncio.sleep(0)` — without it, produce_frames races through every frame in one
  scheduler turn and starves the other stages, since none of the fakes have a real suspension
  point the way ffmpeg/ONNX I/O would. The fake `Tracker` shifts ids by 100 per `reset()` so
  scene-cut tests can tell which scene an id came from.
