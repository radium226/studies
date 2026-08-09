# try-to-stream-video-on-the-fly

A study project that streams a video "live" over HTTP — no HLS, no DASH — by continuously
re-encoding it into **fragmented MP4 (fMP4)** and pushing the raw byte stream to browsers through a
single long-lived HTTP response, which the browser feeds directly into the **MSE (Media Source
Extensions)** API.

On top of that transport, every frame passes through a real computer-vision pipeline:

- **Face detection** with SCRFD (ONNX)
- **Face embedding** with ArcFace (ONNX)
- **Multi-object tracking** with ByteTrack (stable ids across frames)
- **Per-frame spline interpolation** of the sparse detections, so overlays move smoothly at full
  video frame rate even though detection runs only a few times per second

This README is the detailed map of that pipeline: it walks the data from the source file all the
way to the browser, and calls out **every rescale, every coordinate space, and every buffering
point** along the way. For a shorter orientation aimed at editing the code, see `CLAUDE.md`.

---

## Quick start

```bash
# from app/  (a uv-managed Python project)
uv sync
uv run video-streamer                     # starts idle on http://127.0.0.1:8000

# or, from the repo root, with some tuning flags preset (still starts idle):
mise run webapp
```

Then open <http://127.0.0.1:8000> and choose a source in the page (see below).

**System dependencies** (invoked as subprocesses, must be on `PATH`): `ffmpeg`, `ffprobe`, and —
for URL sources — `yt-dlp`. ONNX weights live in `app/models/`.

CLI flags of note (`app.py:main`) are **global tuning knobs only** — the source is chosen at runtime
in the web UI, so there are no source/`--input-video` flags:

| Flag | Meaning |
| --- | --- |
| `--resize-video WxH` | Scale in the decoder before any processing (`-1` on an axis keeps aspect). |
| `--speed-factor N` | Playback speed multiplier (default `1.0`). Scales both the decoder read-rate (`-readrate N`) and the encoder output fps, for a smooth `N×` fast-forward (`0.5` = slow motion). All frames are still decoded/encoded. |
| `--frag-duration-ms N` | Target fMP4 fragment duration (default 200 ms). |
| `--scrfd-batch-frames N` | Frames per batched SCRFD detection pass (default `4`). |
| `--arcface-batch-crops M` | Max face crops per batched ArcFace pass (default `8`). |
| `--max-batch-lag-ms T` | Max ms to wait for a full SCRFD batch before firing with fewer frames (default `0` = fire immediately). Pairs with `--scrfd-batch-frames` to trade lag for fuller GPU batches. |
| `--lookahead K` | Interpolation lookahead in detection snapshots (default `3`). Higher = smoother splines, more end-to-end lag. |

---

## Idle by default

The app **starts idle** — no source, no pipeline, no ffmpeg processes, no models loaded. You pick a
source at runtime from the player page:

- **Paste a video URL** and hit *Load* (resolved to a direct media URL via `yt-dlp`). Tick **loop**
  to repeat it forever; leave it unchecked to play once.
- **Test pattern** starts the built-in synthetic `testsrc` asset (generated on first use).
- **Stop** tears the pipeline back down to idle on demand.

When a source **ends** (a finite, non-looping video reaches its end) the app returns to idle
silently ("No source, waiting for URL"). When a source **fails** mid-stream (a decode/ffmpeg/yt-dlp
crash) it also returns to idle, but a **failure popup** with the error is shown in the browser.
While idle the player blanks the video (no frozen last frame) and hides the loading bar.

There is exactly **one pipeline per process**, shared by all clients — never per-connection.

---

## The pipeline, stage by stage

Everything runs in **one asyncio event loop, in one process**. The only work that leaves the loop
is ONNX inference, which is offloaded to a single-worker `ThreadPoolExecutor`. The whole chain is a
**singleton**: all HTTP clients share one decode → CV → encode pipeline and differ only in which
fragments they have consumed.

```
 ┌─────────────┐   BGR24 frames   ┌────────┐   BGR24 frames   ┌─────────────┐   fMP4 bytes
 │ ffmpeg dec. │ ───────────────► │ Engine │ ───────────────► │ ffmpeg enc. │ ─────────────►
 │ (reader.py) │  (input_video)   │(engine)│    (writer.py)   │ (writer.py) │
 └─────────────┘                  └────────┘                  └─────────────┘
                                                                     │ top-level MP4 boxes
                                                                     ▼
                                            ┌──────────────┐   ┌─────────────┐   HTTP stream
                                            │  BoxReader   │──►│ Broadcaster │ ───────────────►  browser (MSE)
                                            │ (iso_bmff)   │   │(broadcaster)│                    (player.js)
                                            └──────────────┘   └─────────────┘
                                                 (orchestrator.py wires these together)
```

### Coordinate spaces at a glance

The single most important thing to keep straight is which pixel grid a set of coordinates lives in.
There are four:

| Space | Size | Where it's created | Where it's used |
| --- | --- | --- | --- |
| **Source** | source W×H | the source file / stream | only inside the decoder |
| **Working** | post-resize W×H (`video_info`) | decoder `scale=` filter | *everything* in Python: numpy frames, all boxes/landmarks, the encoder input |
| **SCRFD letterbox** | 640×640 | `FaceDetector._preprocess` | SCRFD inference only; outputs are immediately rescaled back to **working** |
| **ArcFace canonical** | 112×112 | `FaceEmbedder._align` | ArcFace inference only; produces an embedding vector, no coordinates leave it |

Everything the tracker, the interpolator, and the overlay drawer touch is in **working** space.
Detection and embedding each drop into their own model-input space and come back out again.

---

### 1. Source acquisition & decode — `input_video.py`, `reader.py`, `sample_asset.py`

**Choosing a source.**

- `synthetic`: `ensure_sample_asset()` generates a 1280×720 @ 25 fps `testsrc` clip with ffmpeg on
  first run (`app/assets/sample.mp4`), so the repo needs no checked-in media.
- `url`: `yt-dlp --get-url` resolves the page URL to a direct media URL.

**Probing.** `probe_video_info()` runs `ffprobe` and returns a `VideoInfo(width, height, fps)` in
**source** space. `fps` is parsed from `r_frame_rate` (`num/den`).

**Rescale #1 — the decoder `scale=` filter.** If `--resize-video` is given, `_resolve_resize()`
first expands any ffmpeg-style `-1` placeholder (keep aspect ratio, rounded to an even number of
pixels), producing the **working** resolution. The decoder ffmpeg then runs
`-vf scale=W:H`, so raw frames leave the decoder already in working space. `video_info` reported by
the loader is the **working** size — this is what every downstream stage keys off.

**Decoder command** (`Reader._decoder_cmd`):

```
ffmpeg [-stream_loop -1] -readrate SPEED -i SOURCE -an [-vf scale=W:H] -f rawvideo -pix_fmt bgr24 pipe:1
```

- `-stream_loop -1` (only when the source was started with the **loop** checkbox ticked): loop the
  source forever.
- `-readrate SPEED` (`SPEED` = `--speed-factor`, default `1.0`): pace how fast ffmpeg *emits*
  decoded frames — `-readrate 1` is exactly `-re` (real time), `2` feeds the pipeline twice as
  fast, `0.5` half as fast. Every frame is still decoded; this only throttles emission. The encoder
  scales its output fps by the same factor (`-r SPEED*native_fps`), so the stream stays
  live-balanced and the browser plays the content `SPEED×` faster.
- `-pix_fmt bgr24`: **first pixel-format decision.** Output is packed 8-bit BGR, 3 bytes/pixel — the
  layout OpenCV expects.

**Buffering point A — frame reassembly.** A pipe `read()` returns arbitrary byte counts, never
aligned to frames. `InputVideoLoader.frames()` computes `frame_size = W*H*3` and calls
`read_exact(frame_size)` (`pipe_io.read_exact`, i.e. `readexactly`) to assemble exactly one frame,
then `np.frombuffer(raw, uint8).reshape((H, W, 3))` — a **zero-copy** view of the pipe buffer as a
BGR24 numpy array. EOF (source ended, not looping) yields `None` and ends the generator.

---

### 2. The Engine — `engine.py`

`Engine.process(frame)` is called once per decoded frame and returns either an overlaid frame or
`None` (while its lookahead buffer is still filling — the caller just skips writing). The Engine's
whole job is to reconcile two very different clocks:

- **Video frame rate** (e.g. 25 fps) — every frame arrives and must eventually be emitted.
- **Detection rate** (a few Hz) — ONNX inference is expensive and runs asynchronously off-thread.

It bridges them with a **token bucket** (throttling), a **tracker** (stable ids), an
**interpolation buffer** (smooth per-frame coordinates), and a **delay buffer** (so overlays land
on the exact frame their coordinates were computed for). Here is one `process()` call in order:

1. **Ingest & delay-buffer the frame** (`_buffer_frame`). `frame_index += 1`; store a
   `PendingFrame(frame, captured_at)` in `pending_frames[frame_index]`.

   > **Buffering point B — `pending_frames` (the delay buffer).** Because interpolation runs with a
   > lookahead (step 6), the coordinates we can draw *now* belong to a frame several detection
   > intervals in the **past**. We must keep those past frames around to draw on them. `pending_frames`
   > is an insertion-ordered `dict[int, PendingFrame]`, capped at `_MAX_PENDING_FRAMES = 600`
   > (drops oldest with a warning if detection stalls badly).

2. **Harvest a finished detection** (`_collect_finished_batch`). If the off-thread detection task
   is done: record how long it took into the token bucket (`record_spend(elapsed * fps)`), then take
   its result — a list of `FrameDetections(frame_index, detections, embeddings)`, **one entry per
   sampled frame in ascending order**. Feed each entry to the **tracker** frame by frame (step 3) so
   ByteTrack ids stay stable across the batch, and `push` each tracked result into the interpolation
   buffer — stamped with **the frame detection ran on**, not the (later) frame it finished on.
   Stamping with the finish frame would shift the whole interpolation timeline forward and make
   boxes trail moving faces.

3. **Schedule a new detection batch — throttled** (`_maybe_schedule_batch`). If no detection is in
   flight *and* `token_bucket.try_acquire(1.0)` succeeds, the engine applies a **wait-or-cap** policy:
   it defers firing until either the window holds ≥ `--scrfd-batch-frames` (N) frames (batch full) or
   `--max-batch-lag-ms` (T) milliseconds have elapsed since the batch first became eligible (cap hit,
   fire with whatever is available). With the default T=0 the batch fires immediately as before.
   `_sample_batch_frames` picks up to N frames evenly spaced across the frames buffered since the
   last pass (always including the newest so consecutive batches stay contiguous); `_detect_batch`
   runs all of them through SCRFD as **one** `(N,3,640,640)` batch, then flattens every face across
   them into ArcFace chunks of ≤ `--arcface-batch-crops` (M) crops, and splits the embeddings back
   per source frame. Crucially the frames passed in are **pristine** (overlays are only ever drawn on
   delayed copies in step 7), so the detector never sees burned-in text. Sampling several frames per
   pass — rather than one — gives gapless real-detection coverage between interpolation control
   points.

   > **Buffering point C — the token bucket** (`token_bucket.py`). Capacity `1.0`, refill rate
   > `fps`. This is a *gate-then-spend* throttle: `try_acquire` only checks that a token is available;
   > the true cost is reported afterward with `record_spend`. Net effect: detection is allowed to
   > consume at most one "real-time frame budget" worth of CPU per frame interval, so a slow machine
   > automatically detects less often instead of falling behind unboundedly.

4. **Advance the interpolation cursor** (start of `_emit_delayed_frame`). `detection_buffer.get()`
   returns an `InterpolatedFrame(frame_idx, faces, is_interpolated)` — called `t_q` below — or
   `None` while the buffer is still accumulating its initial lookahead. `t_q` is a **past
   working-space frame index**; `faces` are the interpolated `TrackedFace`s for that frame;
   `is_interpolated` says whether `t_q` fell strictly between two real detections (dashed box) or
   landed exactly on one (solid box).

5. **Pair coordinates with their frame.** Drop every `pending_frames` entry older than `t_q`, then
   fetch `pending_frames[t_q]`. That frame — the one the coordinates were actually computed for — is
   what we draw on and emit. (If it was already evicted, fall back to the oldest surviving frame.)

6. **Draw & return.** Draw the timestamp/frame-counter text overlay, then the per-face boxes,
   landmarks, and track ids (step 7 detail), and return the result. The Orchestrator sends it to the
   encoder.

The net observable behavior: the stream runs a **fixed few-hundred-millisecond delay** behind the
source, and in exchange every single frame carries smoothly-interpolated overlays even though the
detector only fired a handful of times per second.

---

### 3. Detection & embedding — `detection.py`

Both models run under `onnxruntime` with the **CPU** execution provider (session open/close and the
"must be used as a context manager" guard live in a shared `_OnnxModel` base). This is where the two
model-input coordinate spaces (and their rescales) live. Both models are **batched**: the Engine
hands `detect_batch` a list of N frames and `embed_many` a flat list of `(frame, detection)` pairs,
so one SCRFD run covers all N sampled frames and one ArcFace run (chunked at M crops) covers every
face found across them.

#### FaceDetector — SCRFD (`scrfd_10g_kps_dynamic.onnx`)

Each of the N frames is letterboxed and preprocessed independently, then the tensors are stacked
into one `(N, 3, 640, 640)` batch for a single `session.run`; the per-frame `scale` is kept so each
batch element's detections can be rescaled back to its own working-space pixels.

**Rescale #2 — letterbox to 640×640** (`_preprocess`, per frame; returns `(tensor, scale)` so the
same `scale` is reused to undo the rescale after decoding):

1. `scale = min(640/H, 640/W)` — the single factor that fits the working frame inside 640×640
   without distortion.
2. `cv2.resize` to `(round(W*scale), round(H*scale))`, then paste into the **top-left** of a
   zero-filled 640×640 canvas (letterbox padding on the right/bottom only — this matters for undoing
   it, because there's no offset to subtract, only a scale to divide by).
3. **BGR → RGB** (`[:, :, ::-1]`), cast to float32.
4. **Normalize:** `(pixel - 127.5) / 128.0`.
5. **HWC → CHW** (`transpose(2,0,1)`); the N per-frame tensors are then stacked into the
   `(N, 3, 640, 640)` batch.

**Decode** (`_decode`): SCRFD emits three feature levels at strides 8/16/32, each with
`_SCRFD_NUM_ANCHORS = 2` anchors per cell. For each level: anchor centers are laid out on the
640×640 grid (computed once per stride and cached — they depend only on the fixed input size),
the raw regression outputs are multiplied by the stride to become pixel distances,
`distance → bbox` and `distance → 5 landmarks` convert center-relative distances into absolute
640-space corners/points, and a score threshold (`0.5`) masks out low-confidence cells.

**NMS** (`_nms`): greedy IoU suppression at threshold `0.4`.

**Rescale #2, undone — back to working space.** Every surviving bbox corner and landmark is
divided by `scale` (no offset, thanks to top-left letterboxing). The returned `Detection`s
(`bbox` = `(x1,y1,x2,y2)`, `landmarks` = `(5,2)`, `confidence`) are therefore in **working** pixel
coordinates — the same grid as the numpy frame.

#### FaceEmbedder — ArcFace (`arcface_w600k_r50_batch.onnx`)

`embed_many` takes every `(frame, detection)` pair across the whole batch of sampled frames (each
crop is aligned against **its own** source frame), then runs ArcFace in chunks of at most M crops so
the inference batch stays bounded no matter how many faces were found. For each item:

**Rescale #3 — landmark-driven affine alignment to 112×112** (`_align`):
`cv2.estimateAffinePartial2D` finds the similarity transform mapping the face's 5 detected
landmarks (working space) onto a fixed canonical 5-point template (`_REFERENCE_LANDMARKS`, the
standard ArcFace layout on a 112×112 crop). `cv2.warpAffine` applies it, producing a
pose-normalized 112×112 face. If the estimate fails, fall back to a plain resize.

**Preprocess** (`_preprocess`): **BGR → RGB**, normalize `(pixel - 127.5)/128`, **HWC → CHW**.
Crops are stacked and run through ArcFace in chunks of at most M (`--arcface-batch-crops`).

**Output:** each row is **L2-normalized to a unit vector**. This unit embedding is later used two
ways: as a per-track identity key stored by the tracker, and (its first three components, mapped
`[-1,1] → [0,255]`) as the **overlay color** for that face, so the same face keeps a consistent hue.

---

### 4. Tracking — `tracking.py`

`ByteTracker` wraps `trackers.ByteTrackTracker` (with `supervision` detection containers). It's
called once per *detection* result (a few Hz), not per video frame.

- **Buffered IoU (`BIoU(buffer_ratio=0.5)`).** Because updates arrive at detection cadence, a fast
  face can move nearly its own width between updates, so plain IoU association would see zero overlap
  and churn ids. Buffered IoU inflates boxes before matching to bridge that gap.
  `lost_track_buffer=30` keeps a lost track alive for ~30 update-frames; `track_activation_threshold`
  and `minimum_consecutive_frames=1` make tracks confirm quickly.
- **Ids only, real boxes.** ByteTrack's own output boxes are Kalman *estimates* that drift from the
  detection. So the wrapper takes only the stable `tracker_id`, then re-matches that track box to the
  best-IoU **input detection** (`_best_match`) and keeps *that* detection's box + landmarks — so the
  box and its landmarks stay mutually consistent and glued to the actual face.
- **Embeddings by id.** The matched detection's embedding is cached per track id (and re-served on
  later frames where that face may lack a fresh embedding). Output is a list of
  `TrackedFace(track_id, detection, embedding)` — still in **working** space.

---

### 5. Interpolation with lookahead — `interpolation.py`

`LookaheadTrackBuffer` (configured by `--lookahead`, default 3) turns the sparse stream of tracked snapshots
into a smooth per-video-frame stream of coordinates. This is the component that lets ~5 Hz detection
drive 25 fps overlays.

- **`push(frame_idx, faces)`** stores a `Snapshot` — the tracked faces at a real detection frame,
  keyed by `track_id`.
- **`get()`** is called once per video frame and advances an internal **render cursor** by exactly
  one frame each call. The cursor is deliberately held **behind** the newest snapshot by `lookahead`
  detections, so the frame being rendered always lies *between* two real detections that both exist.
  That guarantees pure **interpolation, never extrapolation** — overlays never overshoot into a
  future that hasn't been detected yet. This lag is exactly why the Engine keeps `pending_frames`.

**Per-face interpolation.** Faces are matched across snapshots **by track id** (never by list
position — detection output is confidence-ordered, so positional matching would blend different
faces whenever ranks flip or a face enters/leaves). For each face a window of up to `lookahead`
snapshots on each side supplies control points `(frame_idx, bbox[4] + landmarks[10])`, and the
target frame `t_q` is evaluated with the chosen scheme:

- `pchip` (default) — monotone cubic (`scipy` `PchipInterpolator`), smooth without overshoot.
- `cubic` — natural cubic spline.
- `linear` — straight-line `np.interp`.

Faces with fewer than two control points pass through un-interpolated. `get()` returns an
`InterpolatedFrame(frame_idx, faces, is_interpolated)`. Snapshots that fall behind the spline
window are dropped so memory stays bounded.

> **Buffering point D — the snapshot list & render cursor.** The buffer holds roughly
> `2*lookahead + a few` snapshots; the cursor's distance behind the newest snapshot *is* the
> pipeline's end-to-end delay.

---

### 6. Overlay drawing — `engine.py` (`_draw_detections`) + `overlay.py`

On a **copy** of the delayed frame `t_q`:

- A text overlay (`draw_overlay`): capture timestamp + `frame t_q`.
- Per face: bounding box (solid `cv2.rectangle` when `is_interpolated` is `False`, i.e. we're on a
  real detection frame; **dashed** via `draw_dashed_rect` when the frame is interpolated), the 5
  landmark points, and the `#track_id` label. Color comes from the face's embedding (consistent hue
  per identity), or gray if no embedding yet.

The frame is still **working**-space BGR24 the whole time.

---

### 7. Encode to fragmented MP4 — `writer.py`

`Writer.write_frame(frame.tobytes())` streams the overlaid BGR24 bytes into the encoder's stdin
(`await drain()` provides backpressure). Encoder command (`_encoder_cmd`):

```
ffmpeg -f rawvideo -pix_fmt bgr24 -s WxH -r FPS -i pipe:0 -an
       -c:v libx264 -profile:v baseline -level 3.0 -pix_fmt yuv420p
       -preset veryfast -tune zerolatency
       -g GOP -keyint_min GOP -sc_threshold 0
       -force_key_frames expr:gte(t,n_forced*2)
       -movflags frag_keyframe+empty_moov+default_base_moof
       -frag_duration FRAG_US -flush_packets 1 -f mp4 pipe:1
```

What matters here:

- **Rescale #4 — pixel format, not resolution.** Resolution is unchanged (working space in, working
  space out); the encoder converts **BGR24 → yuv420p**, the chroma-subsampled format H.264 needs and
  browsers decode.
- **`-r FPS`** is `native_fps * --speed-factor`. At the default `1.0` it matches the source fps so
  playback speed and the encoder PTS clock are correct; a higher factor stamps frames closer together
  so the browser plays the content proportionally faster (the decoder's `-readrate` feeds frames at
  the matching wall-clock rate to keep the stream live-balanced).
- **Fixed GOP on keyframe boundaries.** `GOP = round(fps * KEYFRAME_INTERVAL_SECONDS)` with
  `KEYFRAME_INTERVAL_SECONDS = 2`, plus `-keyint_min`, `-sc_threshold 0`, and forced keyframes every
  2 s. `frag_keyframe` starts a new fragment at each keyframe, so **every fragment begins with an
  IDR** and is independently decodable — essential for MSE and for new clients joining mid-stream.
- **`empty_moov + default_base_moof`** produce a self-contained init segment followed by
  self-describing `moof+mdat` fragments (fragmented MP4). **`zerolatency` + `flush_packets 1`** stop
  ffmpeg from buffering, so fragments appear as soon as they're encoded.
- **`baseline` / `level 3.0` / `avc1.42001e`** — the low-complexity H.264 profile the browser MIME
  in `player.js` advertises; the two must agree.

---

### 8. Box parsing & fan-out — `orchestrator.py`, `iso_bmff.py`, `broadcaster.py`

**Orchestrator** owns the two long-lived asyncio tasks that connect everything:

- `frame-forward`: pulls frames from the loader, runs `Engine.process`, writes non-`None` results
  to the encoder (ends cleanly on `BrokenPipe`/`ConnectionReset` at shutdown). When the source is
  exhausted (a non-looped video ends) it **closes the encoder's stdin**, so ffmpeg flushes its
  trailing fragments and EOFs its stdout.
- `box-parse`: reads encoder stdout and turns the byte stream into publishable units. On encoder
  EOF it **closes the Broadcaster**, ending every client stream cleanly instead of leaving them
  polling a stream with no producer behind it.

If either task crashes (e.g. an ffmpeg process dies), a done-callback logs the exception, **records
it as the pipeline's `failure_reason`**, and closes the Broadcaster immediately — failures surface
as ended client streams, not as a silent stall. `PipelineManager` (see below) watches for exactly
this and takes the pipeline **to idle**, surfacing the recorded reason to the browser as a failure
popup. A clean end-of-source sets no `failure_reason`, so it just goes idle silently.

> **Buffering point E — ISO BMFF box reassembly** (`iso_bmff.BoxReader`). Encoder stdout arrives in
> arbitrary 64 KiB chunks that never align to MP4 box boundaries. `BoxReader.feed()` appends to an
> internal buffer and yields only **complete top-level boxes** (reading each box's 32-bit — or 64-bit
> for size==1 — length prefix). A live stream never emits a size==0 "to EOF" box, so that's treated
> as an error.

The box-parse task classifies boxes:

- Everything up to and including the first **`moov`** (i.e. `ftyp` + `moov`) is concatenated into
  the **init segment** and handed to `Broadcaster.set_init_segment`. This defines codecs, resolution,
  and timescale — a client must receive it before any fragment.
- After that, each **`moof`** is paired with the following **`mdat`** and published together as one
  **media fragment** (`publish_fragment`).

**Broadcaster** is the single-producer / many-consumer hand-off, now built on an
**`asyncio.Condition`** (producer and all HTTP consumers share one event loop, so no thread bridging
is needed):

> **Buffering point F — the fragment ring** (`deque(maxlen=15)`). A bounded deque gives automatic,
> allocation-free **drop-oldest** retention: total memory is capped regardless of how many clients
> connect or how slow they are. Each fragment carries a monotonically increasing `seq`.

- `snapshot_for_new_client()` — a joining client gets the init segment + **only the single latest
  fragment** (a true live join at the edge, not a replay from the start).
- `wait_for_next(last_seq, timeout)` — awaits the next fragment after `last_seq`. If `last_seq` has
  fallen off the back of the deque (a client too slow to keep up), it raises **`LaggedError`**; the
  client is expected to disconnect rather than retry.

---

### 9. HTTP delivery — `app.py`

`GET /{stream_id}.mp4` returns a Starlette `StreamingResponse` (`media_type="video/mp4"`,
`Cache-Control: no-cache`). Each successful pipeline build mints a fresh random UUID
(`PipelineManager` sets `app.state.stream_id`), so every build — a URL source, the test pattern, or
a later switch — gets served at its own never-reused URL. Its async generator:

1. Emits the init segment (retrying once after 0.5 s if the pipeline hasn't produced it yet).
2. Emits the single latest fragment, then loops on `wait_for_next`, yielding each new fragment and
   checking `request.is_disconnected()`. `LaggedError` ends the response, as does the Broadcaster
   closing (end of stream, a switch, or a stop).

If the app is **idle** (no broadcaster), the requested `stream_id` doesn't match the current build's
id, or the current Broadcaster is already closed, the route returns **`410 Gone`** instead of a
stream — the player uses this to tell "the stream ended / went idle" apart from a network error.

The player drives everything off **`GET /api/status`**, which returns
`{state: "idle"|"playing", stream_url, source, error}`. `stream_url` is the current
`/<stream_id>.mp4` path (or `null` when idle); `error` is an id-tagged record of the last mid-stream
failure, which the player shows once as a popup.

Sources are started with **`POST /api/source`**, whose body is either `{"url": ..., "loop": bool}`
(http/https only) or `{"synthetic": true, "loop": bool}` for the test pattern. It builds the whole
pipeline via `PipelineManager.start_url` / `start_synthetic`, returning `{width, height, fps}`. The
build is guarded by a lock (concurrent requests queue) and a 60 s timeout (a hung `yt-dlp`/`ffprobe`
returns `504` instead of holding the lock forever); any build failure returns `400` with the error.
**`POST /api/stop`** calls `go_idle()` to tear the pipeline down to idle on demand.

`GET /` serves the player page; `/static` serves `player.js`, `source.js`, and CSS. The `lifespan`
hands the whole pipeline lifecycle to **`PipelineManager`** (`pipeline.py`): it **starts idle**
(building nothing; `app.state.broadcaster`/`engine`/`stream_id` are all `None`) and builds the
loader → writer → engine → broadcaster → orchestrator stack on demand inside an `AsyncExitStack`.
Builds are **teardown-first**: any old stack is fully closed (closing its Broadcaster, which ends
every client stream) before the new one starts, so only one ffmpeg pair + ONNX engine ever runs at
a time, at the cost of a short client-visible gap that the player bridges by polling.
`SuppressShutdownCancellation` silences the harmless `CancelledError` noise from cutting open
connections on Ctrl-C.

Every successful build also starts a **watchdog** task tagged with a monotonic generation number.
It waits for its broadcaster to close and, if that generation is still the current one (i.e. an
explicit stop or source switch didn't already replace it), takes the pipeline **to idle** —
silently for a clean end-of-source, or recording an id-tagged `last_error` (from the orchestrator's
`failure_reason`) that the player surfaces as a popup. So source exhaustion, an ffmpeg crash, or a
dropped connection all resolve to a clean idle state rather than a stream stuck returning `410`.

---

### 10. Browser playback — `static/player.js`

- Advertises MIME `video/mp4; codecs="avc1.42001e"` (must match the encoder's baseline/level 3.0).
- Polls `GET /api/status` and **gates on state**. While **idle** it blanks the `<video>` (detaches
  the media so no frozen last frame shows — the element falls back to its black background), hides
  the loading bar, shows "No source, waiting for URL", surfaces any new failure through the popup,
  and re-polls every 2 s. When **playing** it creates a `MediaSource`, adds a `SourceBuffer`,
  `fetch`es the status' `stream_url`, and pumps the streamed `response.body` into the buffer through
  a small **append queue** (MSE forbids overlapping `appendBuffer` calls, so a `pump()`/`updateend`
  loop serializes them).

> **Buffering point G — the MSE `SourceBuffer`.** This is the client-side playback buffer. Two
> behaviors keep it healthy:
>
> - **Seek to the live edge once.** The encoder PTS clock runs continuously from *server* start, so
>   a client connecting later sees buffered data starting far past zero; on the first `updateend` the
>   player seeks to `bufferedEnd - 0.1s` and calls `play()`, instead of stalling forever at
>   `currentTime = 0`.
> - **Trim old data.** When buffered *duration* exceeds 60 s, it removes everything up to
>   `end - 30 s`, so a long session doesn't grow the buffer without bound. (It compares duration, not
>   the absolute — and unbounded — end timestamp.)

> - **Survive quota pressure.** `appendBuffer` can throw `QuotaExceededError` when the browser's
>   buffer is full; the player puts the chunk back on its queue, evicts the older half of the
>   buffered range, and resumes on the follow-up `updateend` instead of stalling permanently.

A progress bar reflects the OR of two independent "loading" reasons: the connect phase and the
`<video>` element starving for data mid-stream (`waiting`/`playing` events); it stays hidden while
idle. On a `410 Gone` (the stream ended, failed, or was stopped, so we're now idle) it blanks the
frame and drops back to the idle status poll; on a mid-stream fetch drop it shows "reconnecting…"
and re-polls after 1 s. Each play builds a fresh `MediaSource` and revokes the previous object URL.

`static/source.js` wires the source form: *Load* POSTs `{url, loop}`, *Test pattern* POSTs
`{synthetic: true, loop}` to `/api/source`, and *Stop* POSTs `/api/stop`. It reports the result (or
error) in the status line, and shows build failures in a dismissible `#source-error` panel; it also
exposes `window.showSourceError` so player.js can raise the same popup for async mid-stream failures.
The stream handoff itself rides on player.js's status-poll loop — the old stream ends when the old
Broadcaster closes, and polling picks up the new one (or the idle state).

---

## End-to-end summary of transformations

| # | Stage | Operation | Input space | Output space |
| --- | --- | --- | --- | --- |
| 1 | Decoder resize (optional) | `scale=` filter | source | **working** |
| — | Decoder pixel format | packed 8-bit BGR | — | BGR24 |
| A | Frame reassembly | `read_exact` + `reshape` | byte stream | `(H,W,3)` numpy |
| 2 | SCRFD preprocess | letterbox + normalize + BGR→RGB + HWC→CHW | working | 640×640 |
| 2⁻¹ | SCRFD postprocess | decode + NMS + divide by scale | 640×640 | **working** |
| 3 | ArcFace align | 5-landmark affine warp | working | 112×112 |
| — | ArcFace output | L2-normalized vector | 112×112 | embedding |
| 4 | Tracking | ByteTrack ids + real detection boxes | working | working + ids |
| 5 | Interpolation | per-frame PCHIP with lookahead lag | working (sparse) | working (dense) |
| 6 | Overlay | text + boxes/landmarks/ids on delayed frame | working | working |
| 7 | Encoder pixel format | BGR24 → yuv420p, H.264 baseline | working | fMP4 bytes |
| E | Box reassembly | `BoxReader` | byte stream | MP4 boxes |
| F | Fan-out | init segment + bounded fragment deque | boxes | HTTP stream |
| G | MSE buffer | append + seek-to-edge + trim | HTTP stream | decoded video |

**Buffering points, at a glance:** (A) frame reassembly · (B) `pending_frames` delay buffer ·
(C) detection token bucket · (D) interpolation snapshot window / render-cursor lag · (E) ISO BMFF
box reassembly · (F) broadcaster fragment ring · (G) browser MSE `SourceBuffer`.

The end-to-end latency of the stream is dominated by (B)+(D) (the interpolation lookahead, a few
hundred ms) plus (F)+(G) (a fragment or two of buffering on each side of the network).
