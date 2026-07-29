"""Decorates a `FrameBroadcaster`: requests an early pipeline stop once the
first face track reaches `min_track_frames` *rendered* frames — the definition
of a track worth stopping for, measured in video frames (exact, interpolated,
and held appearances all count, since they are all frames the track is visibly
on screen). Counting happens here, on the output side, because interpolated
frames only exist downstream of the render cursor — a tracker-level condition
can only ever count sparse detection snapshots.

The stop itself is graceful per the `StopToken` contract: frames already read
keep flowing through the pipeline, so the track keeps growing past N while the
tail drains. Per-track counts reset at scene cuts (`frame.is_scene_start`) —
identities never survive a cut, and ByteTrack may reuse ids after its own
reset, so a count must never pool frames from both sides of one."""

from __future__ import annotations

from video_analyzer import kernel


class StopOnFirstTrack[FrameContentT, FaceEmbeddingT](
    kernel.FrameBroadcaster[FrameContentT, kernel.TrackedFace[FaceEmbeddingT]]
):

    def __init__(
        self,
        wrapped: kernel.FrameBroadcaster[
            FrameContentT, kernel.TrackedFace[FaceEmbeddingT]
        ],
        stop_token: kernel.StopToken,
        min_track_frames: int = 1,
    ) -> None:
        self._wrapped = wrapped
        self._stop_token = stop_token
        self._min_track_frames = min_track_frames
        self._frames_by_track: dict[int, int] = {}

    async def broadcast_frame(
        self,
        annotated_frame: kernel.AnnotatedFrame[
            FrameContentT, kernel.TrackedFace[FaceEmbeddingT]
        ],
    ) -> None:
        await self._wrapped.broadcast_frame(annotated_frame)
        if annotated_frame.frame.is_scene_start:
            self._frames_by_track.clear()
        for tracked_face in annotated_frame.faces:
            count = self._frames_by_track.get(tracked_face.track_id, 0) + 1
            self._frames_by_track[tracked_face.track_id] = count
            if count >= self._min_track_frames:
                self._stop_token.request_stop()
