from typing import NamedTuple

from .models import FrameIndex, Snapshot, TrackedFace
from .services import Interpolator


class AdvanceResult[FaceEmbeddingT](NamedTuple):
    frame_index: FrameIndex
    is_exact: bool
    # The two real-detection snapshots the faces were interpolated between, or
    # None when there was nothing to interpolate (a finished single-snapshot
    # stream) and the snapshot's own faces are simply held.
    bracket: tuple[Snapshot[TrackedFace[FaceEmbeddingT]], Snapshot[TrackedFace[FaceEmbeddingT]]] | None
    faces: list[TrackedFace[FaceEmbeddingT]]


class RenderCursor[FaceEmbeddingT]:
    """Walks frame indices sequentially through the interpolation window,
    lagging `lookahead_snapshots` snapshots behind the newest tracked snapshot
    so every frame it emits lies strictly between two real detections — never
    extrapolated.

    `advance()` returns the next not-yet-emitted frame index while one is
    reachable inside the usable window, and None once the cursor has caught up
    — it never returns the same index twice and never skips one, so a caller
    looping `while (result := await cursor.advance()) is not None` renders
    every frame exactly once: nothing when detections stall, a catch-up burst
    when they resume.

    `finish()` declares that no further snapshot will ever arrive (end of
    stream): the lookahead requirement is dropped and the remaining snapshots
    are walked to the very last one, so the stream's tail is rendered instead
    of being abandoned inside the lookahead window. Frames *beyond* the last
    snapshot are the caller's to flush — the cursor doesn't know how many
    exist.

    This holds only the *timing* bookkeeping (which segment, which frame index
    to render, when to prune old snapshots) — the actual coordinate math is
    delegated to the injected `Interpolator`, which is pure numeric fill.
    """

    def __init__(
        self,
        lookahead_snapshots: int,
        interpolator: Interpolator[TrackedFace[FaceEmbeddingT]],
    ) -> None:
        self._lookahead_snapshots = lookahead_snapshots
        self._interpolator = interpolator
        self._snapshots: list[Snapshot[TrackedFace[FaceEmbeddingT]]] = []
        self._segment_index = 0
        self._next_index: FrameIndex | None = None
        self._finished = False

    def push_snapshot(self, snapshot: Snapshot[TrackedFace[FaceEmbeddingT]]) -> None:
        self._snapshots.append(snapshot)

    def finish(self) -> None:
        """No further snapshot will arrive: let `advance()` walk the remaining
        snapshots without holding back the lookahead margin."""
        self._finished = True

    @property
    def _max_segment_end(self) -> int:
        """Index (into `_snapshots`) of the newest snapshot usable as a segment
        end. While live, `lookahead_snapshots` snapshots must remain beyond it
        (that margin is what keeps every emitted frame strictly interpolated);
        once finished, every snapshot is usable."""
        if self._finished:
            return len(self._snapshots) - 1
        return len(self._snapshots) - 1 - self._lookahead_snapshots

    async def advance(self) -> AdvanceResult[FaceEmbeddingT] | None:
        max_segment_end = self._max_segment_end
        if max_segment_end < 1:
            # No full segment inside the usable window yet. A finished stream
            # whose only snapshot never got a partner still emits that one
            # frame (held, nothing to interpolate); everything else waits.
            if self._finished and self._snapshots:
                return self._advance_on_single_snapshot()
            return None

        if self._next_index is None:
            self._next_index = self._snapshots[0].frame_index
        if self._next_index > self._snapshots[max_segment_end].frame_index:
            # Caught up with the newest interpolatable frame — nothing new to
            # emit until more snapshots land (or `finish()` widens the window).
            return None

        # Advance to the segment containing `_next_index`, keeping the segment
        # end inside the usable window.
        while (
            self._next_index > self._snapshots[self._segment_index + 1].frame_index
            and self._segment_index + 1 < max_segment_end
        ):
            self._segment_index += 1
        self._prune_stale_snapshots()

        segment_start = self._snapshots[self._segment_index]
        segment_end = self._snapshots[self._segment_index + 1]
        frame_index = self._next_index
        is_exact = frame_index in (segment_start.frame_index, segment_end.frame_index)
        faces = await self._interpolate(frame_index)
        self._next_index += 1
        return AdvanceResult(
            frame_index=frame_index,
            is_exact=is_exact,
            bracket=(segment_start, segment_end),
            faces=faces,
        )

    def _advance_on_single_snapshot(self) -> AdvanceResult[FaceEmbeddingT] | None:
        snapshot = self._snapshots[0]
        if self._next_index is None:
            self._next_index = snapshot.frame_index
        if self._next_index > snapshot.frame_index:
            return None
        frame_index = self._next_index
        self._next_index += 1
        return AdvanceResult(
            frame_index=frame_index,
            is_exact=frame_index == snapshot.frame_index,
            bracket=None,
            faces=list(snapshot.faces),
        )

    def _prune_stale_snapshots(self) -> None:
        # Snapshots behind the spline window can never be used again.
        drop = self._segment_index - self._lookahead_snapshots
        if drop > 0:
            del self._snapshots[:drop]
            self._segment_index -= drop

    async def _interpolate(
        self, frame_index: FrameIndex
    ) -> list[TrackedFace[FaceEmbeddingT]]:
        """Interpolate every face of the current segment's start snapshot,
        matching control points across snapshots by track id."""
        segment_start = self._snapshots[self._segment_index]

        window_start = max(0, self._segment_index - self._lookahead_snapshots)
        window_end = min(
            len(self._snapshots), self._segment_index + self._lookahead_snapshots + 2
        )
        window = self._snapshots[window_start:window_end]
        span_start = window[0].frame_index
        span = window[-1].frame_index - span_start + 1

        results: list[TrackedFace[FaceEmbeddingT]] = []
        for tracked_face in segment_start.faces:
            track_id = tracked_face.track_id
            slots: list[TrackedFace[FaceEmbeddingT] | None] = [None] * span
            known = 0
            for snapshot in window:
                match = next(
                    (
                        candidate_face
                        for candidate_face in snapshot.faces
                        if candidate_face.track_id == track_id
                    ),
                    None,
                )
                if match is not None:
                    slots[snapshot.frame_index - span_start] = match
                    known += 1
            if known < 2:
                # A track seen only once in the window has nothing to
                # interpolate between — hold it still at its known position
                # rather than dropping or extrapolating it.
                results.append(tracked_face)
                continue
            filled = await self._interpolator.interpolate(slots)
            results.append(filled[frame_index - span_start])
        return results
