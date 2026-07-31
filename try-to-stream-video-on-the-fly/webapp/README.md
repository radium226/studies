# video-analyzer-webapp

A Starlette web app: pick a local video file from a server-side directory, pick a stop strategy,
and watch it stream live in the browser (MSE / fragmented MP4) with detected/tracked faces drawn
on it. Wires [`video-analyzer-core`](../core)'s real SCRFD/ArcFace/ByteTrack/PCHIP/
histogram-scene-cut backends into [`video-analyzer-kernel`](../kernel)'s `Pipeline`, the same way
[`video-analyzer-cli`](../cli) does for a local `ffplay` window — this project's own contribution
is the browser-facing transport: an fMP4 encoder sink with overlay drawing, an HTTP fan-out
broadcaster, and the Starlette routes/UI tying it together.

```bash
uv run video-analyzer-webapp                          # http://127.0.0.1:8000, defaults
uv run video-analyzer-webapp --dump-config > run.yaml  # every knob, at its default
uv run video-analyzer-webapp --config run.yaml         # ...and run with it
```

The app starts idle. Open the page, pick a file from the list (served from
`video_library.directory` in the config — defaults to `../app/assets`), optionally arm one or
both stop strategies, and hit Load.

See `CLAUDE.md` for the architecture.
