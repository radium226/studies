# video-analyzer-core

Concrete implementations of [`video-analyzer-kernel`](../kernel)'s service contracts:
real ffmpeg-backed frame I/O, SCRFD face detection, ArcFace face embedding, ByteTrack
multi-object tracking, PCHIP/cubic/linear spline interpolation, and histogram-correlation
scene-cut detection.

`kernel` stays dependency-free (contracts, data shapes, orchestration timing only); every
numpy/scipy/opencv/onnxruntime-touching algorithm lives here instead.
