# video-analyzer-core

Concrete implementations of [`video-analyzer-kernel`](../kernel)'s service contracts:
real ffmpeg-backed frame I/O, SCRFD face detection, ArcFace face embedding, ByteTrack
multi-object tracking, and PCHIP/cubic/linear spline interpolation.

`kernel` stays dependency-free (contracts, data shapes, orchestration timing only); every
numpy/scipy/opencv/onnxruntime-touching algorithm lives here instead.
