# video-analyzer-cli

A small runnable example that plays a local video file — or an http(s) URL, resolved to a
direct media URL via `yt-dlp` — with detected and tracked faces drawn on it, via `ffplay`. Wires [`video-analyzer-core`](../core)'s real
SCRFD/ArcFace/ByteTrack/PCHIP/histogram-scene-cut backends into
[`video-analyzer-kernel`](../kernel)'s `Pipeline`, and adds its own `ffplay`-piping `FrameSink`
plus a no-op `FrameBroadcaster` stub for the one slot neither `kernel` nor `core` implement
(scene detection is real and on by default; `scene_detector: null` swaps in a no-op).

```bash
uv run video-analyzer-cli ../app/assets/sample.mp4

# Or hand it a page URL — yt-dlp (a binary on PATH, like ffmpeg) resolves it to a
# direct media URL first.
uv run video-analyzer-cli https://www.youtube.com/watch?v=aqz-KE-bpKQ
```

Closing the `ffplay` window ends the run: the pipeline stops reading, drains the frames it
already holds, and exits — including the per-track replay below, which is skipped rather than
opening a new window in place of the one you just closed.

There are exactly two command-line options: `--config`, and `--dump-config`. Every tuning knob in
the stack — playback speed, detection batching, interpolation lookahead, the model paths, the
detector/tracker/interpolator settings, the early-stop and track-replay features — lives in a YAML
document instead of a flag. Start one from the built-in defaults:

```bash
uv run video-analyzer-cli --dump-config > run.yaml
```

That prints every section and key filled in with exactly what a bare run uses, so it doubles as
the schema reference. Edit it down to just what you want to change — anything you leave out keeps
its default — and pass it back:

```bash
uv run video-analyzer-cli ../app/assets/sample.mp4 --config run.yaml
```

```yaml
# Watch it 4x faster. Every frame is still decoded and drawn; the speed-up is paid for
# with detection coverage (~1/4 of frames detected, interpolation fills the rest).
frame_source:
  read_rate: 4.0

# ...and spend a bigger detection batch per pass to buy some of that coverage back.
pipeline:
  batch_gate:
    max_frames: 16

# Stop once a face has been on screen for 30 rendered frames, then replay each
# discovered track through its own ffplay window.
stop_strategy:
  on_first_track:
    enabled: true
    min_track_frames: 30
track_recording:
  crop_size: 160
  max_crops_per_track: 60
```

A section that is `null` (or simply absent, for `track_recording`) means that feature is off;
giving it any mapping — even `{}` — turns it on. The early-stop strategies under `stop_strategy:`
are the exception: they are always present, so a dumped config shows what each one can be told,
and each is armed with its own `enabled: true` instead. Arm as many as you like — the first to
fire ends the run.
Unknown keys are a hard error rather than a silent no-op, and the message names the full path
(`pipeline.batch_gate has unknown keys: max_framez`). `CLAUDE.md` walks the schema section by
section and explains why the detection budget deliberately does *not* scale with `read_rate`.

Relative paths (the video, `--config`, and the `models:` entries) passed directly to `uv run`
resolve against this directory, since that's where it executes from. `mise run cli -- <video>`
resolves the video path against the repo root instead — see `CLAUDE.md` for details.
