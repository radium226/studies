# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A study project: stream a video "live" over HTTP by continuously re-encoding it into fragmented
MP4 and pushing fragments to browsers via the MSE (Media Source Extensions) API — no HLS/DASH,
just a raw byte stream over a single long-lived HTTP response.

## Commands

All commands run from the `app/` directory (a `uv`-managed Python project) unless noted.

```bash
uv run video-streamer        # run the app directly (equivalent to the mise task below)
mise run webapp               # from repo root: runs `uv --directory=app run video-streamer`
uv sync                       # install/update dependencies from uv.lock
```

The app listens on `http://127.0.0.1:8000`. There is no test suite, linter, or formatter
configured in this project yet.

### System dependencies

`ffmpeg` and `ffprobe` must be on `PATH` — they are invoked as subprocesses (not Python
packages) for decoding, encoding, and probing video, and for generating the synthetic sample
asset on first run.

## Architecture

Data flows through two ffmpeg subprocesses and a thread-safe fan-out buffer, then out to
browsers over HTTP:

```
ffmpeg decoder (loop source .mp4 -> raw BGR24 frames on stdout)
        -> frame-forward thread: numpy reshape -> overlay.draw_overlay() -> stdin of encoder
ffmpeg encoder (raw BGR24 -> fragmented MP4 on stdout: frag_keyframe+empty_moov+default_base_moof)
        -> box-parse thread: iso_bmff.BoxReader splits the byte stream into top-level MP4 boxes
        -> Broadcaster: holds the init segment (ftyp+moov) + a bounded ring of moof+mdat fragments
        -> Starlette StreamingResponse per client: snapshot (init + latest fragment) then
           live-tail new fragments as they publish
        -> browser: MediaSource + SourceBuffer (static/player.js) appends the byte stream directly
```

Key files (`app/src/video_streamer/`):

- **`reader.py`** — owns both ffmpeg subprocesses and the two worker threads described above.
  `probe_video_info()` uses `ffprobe` to get width/height/fps, which drive both the decoder's
  raw frame size and the encoder's GOP settings (`KEYFRAME_INTERVAL_SECONDS`).
- **`iso_bmff.py`** — minimal ISO BMFF (MP4 box) parser. Exists because pipe reads never align
  to box boundaries; `BoxReader.feed()` buffers until whole boxes are available.
- **`broadcaster.py`** — the single producer / many async consumers hand-off point. Runs on a
  `threading.Condition`, not asyncio primitives, since the reader thread is a plain OS thread
  (blocking subprocess I/O). Starlette handlers bridge the blocking wait via
  `asyncio.to_thread` so one slow client can't block the event loop or other clients. A bounded
  `deque` gives automatic drop-oldest retention (`max_fragments`), capping memory regardless of
  subscriber count/speed. `wait_for_next()` raises `LaggedError` when a client's last-seen
  sequence has fallen off the back of the deque — the client is expected to disconnect, not retry.
  New clients get `snapshot_for_new_client()`: the init segment + only the single latest
  fragment (true live join, not rewind-from-start).
- **`overlay.py`** — trivial OpenCV per-frame overlay (timestamp + frame counter), mainly there
  to prove frames actually flow through numpy/OpenCV rather than being piped through untouched.
- **`sample_asset.py`** — generates a synthetic test video with ffmpeg's `lavfi testsrc` on
  first run so the repo needs no checked-in media beyond `app/assets/sample.mp4`.
- **`app.py`** — Starlette app wiring. `lifespan` starts the `Reader`/`Broadcaster` pair once at
  process startup (single shared source, not per-request). `/stream.mp4` is the fragment feed
  consumed by the frontend; `/` serves the player page.
- **`static/player.js`** — browser side: opens a `MediaSource`, fetches `/stream.mp4` as a
  streamed `Response.body`, and appends chunks to a `SourceBuffer` via a small queue/pump loop.
  On `sourceopen` it seeks to the live edge (encoder PTS runs continuously from server start, not
  from client connect time) and periodically trims old buffered ranges so long sessions don't
  grow the buffer unbounded.

## Working in this codebase

- The Reader/Broadcaster pair is a singleton per process (created once in `app.py`'s
  `lifespan`), not one per client — all clients share the same decode/encode pipeline and only
  differ in which fragments they've consumed.
- Encoder settings (`_encoder_cmd` in `reader.py`) are tuned for MSE compatibility: baseline
  H.264 profile, `frag_keyframe+empty_moov+default_base_moof` for self-contained fragments, and
  a fixed GOP aligned to `KEYFRAME_INTERVAL_SECONDS` so fragment boundaries land on keyframes.
