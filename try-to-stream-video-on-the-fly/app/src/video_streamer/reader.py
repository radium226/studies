"""Decode a looped video with ffmpeg, run each frame through numpy/OpenCV, re-encode to fMP4.

Two ffmpeg subprocesses are kept running for the lifetime of the app:

  decoder: reads the (looped) source file, outputs raw BGR24 frames on stdout.
  encoder: reads raw BGR24 frames on stdin, outputs fragmented MP4 on stdout.

A frame-forwarding thread bridges decoder -> numpy -> overlay -> encoder.
A second thread parses the encoder's fMP4 byte stream into ISO BMFF boxes
and publishes init-segment/fragments to the shared Broadcaster.
"""

from __future__ import annotations

import json
import logging
import random
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from video_streamer.broadcaster import Broadcaster
from video_streamer.iso_bmff import BoxReader, BoxType
from video_streamer.overlay import draw_overlay

logger = logging.getLogger(__name__)

KEYFRAME_INTERVAL_SECONDS = 2
INPUT_LAG_MIN_SECONDS = 0.0
INPUT_LAG_MAX_SECONDS = 0.25
DEFAULT_FRAG_DURATION_MS = 200


def probe_video_info(path: Path) -> tuple[int, int, float]:
    output = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,r_frame_rate",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    stream = json.loads(output)["streams"][0]
    num, den = stream["r_frame_rate"].split("/")
    fps = float(num) / float(den)
    return int(stream["width"]), int(stream["height"]), fps


def _read_exact(stream, size: int) -> bytes | None:
    buf = bytearray()
    while len(buf) < size:
        chunk = stream.read(size - len(buf))
        if not chunk:
            return None
        buf += chunk
    return bytes(buf)


class Reader:
    def __init__(
        self,
        broadcaster: Broadcaster,
        source_path: Path,
        *,
        simulate_input_lag: bool = False,
        frag_duration_ms: int = DEFAULT_FRAG_DURATION_MS,
    ) -> None:
        self._broadcaster = broadcaster
        self._source_path = source_path
        self._simulate_input_lag = simulate_input_lag
        self._frag_duration_ms = frag_duration_ms
        self._stop_event = threading.Event()
        self._decoder: subprocess.Popen | None = None
        self._encoder: subprocess.Popen | None = None
        self._threads: list[threading.Thread] = []
        self._width = 0
        self._height = 0
        self._fps = 0.0

    def start(self) -> None:
        self._width, self._height, self._fps = probe_video_info(self._source_path)
        frame_size = self._width * self._height * 3

        self._decoder = subprocess.Popen(
            self._decoder_cmd(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0
        )
        self._encoder = subprocess.Popen(
            self._encoder_cmd(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )

        self._threads = [
            threading.Thread(
                target=self._drain_stderr, args=(self._decoder, "decoder"), daemon=True
            ),
            threading.Thread(
                target=self._drain_stderr, args=(self._encoder, "encoder"), daemon=True
            ),
            threading.Thread(
                target=self._forward_frames, args=(frame_size,), daemon=True, name="frame-forward"
            ),
            threading.Thread(target=self._read_encoder_output, daemon=True, name="box-parse"),
        ]
        for thread in self._threads:
            thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        for proc in (self._decoder, self._encoder):
            if proc is not None:
                proc.terminate()
        for proc in (self._decoder, self._encoder):
            if proc is None:
                continue
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        for thread in self._threads:
            thread.join(timeout=timeout)
        self._broadcaster.close()

    def _decoder_cmd(self) -> list[str]:
        return [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-stream_loop",
            "-1",
            "-re",
            "-i",
            str(self._source_path),
            "-an",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "bgr24",
            "pipe:1",
        ]

    def _encoder_cmd(self) -> list[str]:
        gop = max(1, round(self._fps * KEYFRAME_INTERVAL_SECONDS))
        return [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "bgr24",
            "-s",
            f"{self._width}x{self._height}",
            "-r",
            str(self._fps),
            "-i",
            "pipe:0",
            "-an",
            "-c:v",
            "libx264",
            "-profile:v",
            "baseline",
            "-level",
            "3.0",
            "-pix_fmt",
            "yuv420p",
            "-preset",
            "veryfast",
            "-tune",
            "zerolatency",
            "-g",
            str(gop),
            "-keyint_min",
            str(gop),
            "-sc_threshold",
            "0",
            "-force_key_frames",
            f"expr:gte(t,n_forced*{KEYFRAME_INTERVAL_SECONDS})",
            "-movflags",
            "frag_keyframe+empty_moov+default_base_moof",
            "-frag_duration",
            str(self._frag_duration_ms * 1000),
            "-flush_packets",
            "1",
            "-f",
            "mp4",
            "pipe:1",
        ]

    def _forward_frames(self, frame_size: int) -> None:
        frame_index = 0
        assert self._decoder is not None and self._encoder is not None
        while not self._stop_event.is_set():
            raw = _read_exact(self._decoder.stdout, frame_size)
            if raw is None:
                break
            frame = np.frombuffer(raw, dtype=np.uint8).reshape((self._height, self._width, 3))
            timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
            frame = draw_overlay(frame, f"{timestamp}  frame {frame_index}")
            if self._simulate_input_lag:
                time.sleep(random.uniform(INPUT_LAG_MIN_SECONDS, INPUT_LAG_MAX_SECONDS))
            try:
                self._encoder.stdin.write(frame.tobytes())
            except (BrokenPipeError, ValueError):
                break
            frame_index += 1

    def _read_encoder_output(self) -> None:
        assert self._encoder is not None
        box_reader = BoxReader()
        init_boxes: list[bytes] = []
        have_init = False
        pending_moof: bytes | None = None

        while not self._stop_event.is_set():
            chunk = self._encoder.stdout.read(65536)
            if not chunk:
                break
            for box_type, raw in box_reader.feed(chunk):
                if not have_init:
                    init_boxes.append(raw)
                    if box_type == BoxType.MOOV:
                        self._broadcaster.set_init_segment(b"".join(init_boxes))
                        have_init = True
                        init_boxes = []
                    continue
                if box_type == BoxType.MOOF:
                    pending_moof = raw
                elif box_type == BoxType.MDAT:
                    if pending_moof is not None:
                        self._broadcaster.publish_fragment(pending_moof + raw)
                        pending_moof = None
                    else:
                        logger.warning("mdat box with no preceding moof, dropping")
                else:
                    logger.debug("ignoring unexpected top-level box %r after init segment", box_type)

    @staticmethod
    def _drain_stderr(proc: subprocess.Popen, name: str) -> None:
        for line in iter(proc.stderr.readline, b""):
            logger.debug("%s: %s", name, line.decode(errors="replace").rstrip())
