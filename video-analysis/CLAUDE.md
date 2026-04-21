# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Video analysis tool that detects and tracks faces in video files, extracting per-face cropped video clips. Uses DeepFace for face detection and Supervision's ByteTrack for multi-object tracking. Video I/O is handled via ffmpeg/ffprobe/ffplay subprocesses with raw frame piping.

## Commands

- **Install dependencies**: `uv sync`
- **Run the app**: `uv run analyse-video` (entry point: `video_analysis:cli`)
- **Run directly**: `uv run python -m video_analysis`
- **Run tests**: `uv run pytest` (single test: `uv run pytest tests/test_foo.py::test_name`)
- **Lint**: `uv run ruff check`
- **Type check**: `uv run ty check`

## Architecture

- `src/video_analysis/__main__.py` — Main pipeline: reads video frames via ffmpeg subprocess, runs face detection every N frames (locally via DeepFace or remotely via WebSocket), tracks faces with ByteTrack, interpolates bounding boxes between detections, writes per-face cropped MP4s via ffmpeg, and displays annotated frames via ffplay.
- `runpod_worker/` — Standalone GPU-based face detection server. A WebSocket service (`server.py`) that receives JPEG-encoded frames, runs DeepFace detection, and returns face coordinates. Packaged as a Docker image with CUDA support for deployment on RunPod.

The client connects to the worker via `RUNPOD_WS_URL` env var; if unset, detection runs locally.

## Key Dependencies

- **DeepFace** with RetinaFace backend for face detection
- **Supervision** (`sv`) for ByteTrack multi-object tracking and `Detections` data structure
- **OpenCV** (`cv2`) for image processing
- System requirement: `ffmpeg`, `ffprobe`, `ffplay` must be on PATH
