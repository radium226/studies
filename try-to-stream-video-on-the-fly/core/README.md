# video-analyzer-core

Concrete implementations of [`video-analyzer-kernel`](../kernel)'s service contracts:
real ffmpeg-backed frame I/O, SCRFD face detection, ArcFace face embedding, ByteTrack
multi-object tracking, PCHIP/cubic/linear spline interpolation, and histogram-correlation
scene-cut detection — plus a `yt-dlp`-backed URL resolver (`resolve_direct_media_url`) that
turns a page URL into a direct media URL `FfmpegFrameSource` can read.

`kernel` stays dependency-free (contracts, data shapes, orchestration timing only); every
numpy/scipy/opencv/onnxruntime-touching algorithm lives here instead.
