from collections import deque
from typing import NamedTuple

from .models import FrameIndex, Snapshot, TrackedFace
from .services import Interpolator


class AdvanceResult[FaceEmbeddingT](NamedTuple):
    frame_index: FrameIndex
    is_exact: bool
    # The two real-detection snapshots the faces were interpolated between, or
    # None for a frame with nothing to interpolate: one held across an
    # inter-scene gap, or landing on a snapshot that never got a partner.
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

    Snapshots flagged `is_scene_start` open a new scene. Interpolation never
    bridges scenes: the old scene finishes in relaxed mode (its own snapshots
    walked to the last one, no lookahead margin needed since no more will
    join it), the frames between its last snapshot and the cut are emitted
    with the old scene's final faces held, and the new scene then warms up
    under the normal lookahead rules.

    `finish()` declares that no further snapshot will ever arrive (end of
    stream): the final scene is closed the same way a scene cut closes one,
    so the stream's tail is rendered instead of being abandoned inside the
    lookahead window. Frames *beyond* the last snapshot are the caller's to
    flush — the cursor doesn't know how many exist.

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
        # Snapshots partitioned by scene, oldest scene first. Only the oldest
        # is ever rendered from; later ones queue up behind their cut.
        self._scenes: deque[list[Snapshot[TrackedFace[FaceEmbeddingT]]]] = deque()
        self._segment_index = 0
        self._next_index: FrameIndex | None = None
        self._finished = False

    def push_snapshot(self, snapshot: Snapshot[TrackedFace[FaceEmbeddingT]]) -> None:
        if not self._scenes or snapshot.is_scene_start:
            self._scenes.append([snapshot])
        else:
            self._scenes[-1].append(snapshot)

    def finish(self) -> None:
        """No further snapshot will arrive: let `advance()` walk the remaining
        snapshots without holding back the lookahead margin."""
        self._finished = True

    async def advance(self) -> AdvanceResult[FaceEmbeddingT] | None:
        while True:
            if not self._scenes:
                return None
            scene = self._scenes[0]
            # A scene is closed once nothing can ever join it — a newer scene
            # exists behind a cut, or the whole stream is over. Closed scenes
            # need no lookahead margin: all their snapshots are final, so
            # segments may run to the very last one and remain interpolation.
            scene_is_closed = len(self._scenes) > 1 or self._finished

            if self._next_index is None:
                self._next_index = scene[0].frame_index

            if self._next_index > scene[-1].frame_index:
                if not scene_is_closed:
                    # Caught up with the newest snapshot; more may still join
                    # this scene.
                    return None
                if len(self._scenes) > 1:
                    next_scene_start = self._scenes[1][0].frame_index
                    if self._next_index < next_scene_start:
                        # The gap between the old scene's last detection and
                        # the cut: still old-scene content, held at its final
                        # known positions.
                        return self._emit_unbracketed(scene[-1], is_exact=False)
                    # The old scene is spent — start rendering the next one.
                    self._scenes.popleft()
                    self._segment_index = 0
                    continue
                # Finished and past the last snapshot: the tail is the
                # caller's to flush (the cursor doesn't know how long it is).
                return None

            max_segment_end = (
                len(scene) - 1
                if scene_is_closed
                else len(scene) - 1 - self._lookahead_snapshots
            )
            if max_segment_end < 1:
                if scene_is_closed and len(scene) == 1:
                    # A closed scene whose only snapshot never got a partner:
                    # emit that one frame as-is, nothing to interpolate.
                    return self._emit_unbracketed(scene[0], is_exact=True)
                return None
            if self._next_index > scene[max_segment_end].frame_index:
                # Caught up with the newest interpolatable frame — wait for
                # the lookahead margin to move.
                return None

            # Advance to the segment containing `_next_index`, keeping the
            # segment end inside the usable window.
            while (
                self._next_index > scene[self._segment_index + 1].frame_index
                and self._segment_index + 1 < max_segment_end
            ):
                self._segment_index += 1
            self._prune_stale_snapshots(scene)

            segment_start = scene[self._segment_index]
            segment_end = scene[self._segment_index + 1]
            frame_index = self._next_index
            is_exact = frame_index in (
                segment_start.frame_index,
                segment_end.frame_index,
            )
            faces = await self._interpolate(scene, frame_index)
            self._next_index += 1
            return AdvanceResult(
                frame_index=frame_index,
                is_exact=is_exact,
                bracket=(segment_start, segment_end),
                faces=faces,
            )

    def _emit_unbracketed(
        self, snapshot: Snapshot[TrackedFace[FaceEmbeddingT]], *, is_exact: bool
    ) -> AdvanceResult[FaceEmbeddingT]:
        assert self._next_index is not None
        frame_index = self._next_index
        self._next_index += 1
        return AdvanceResult(
            frame_index=frame_index,
            is_exact=is_exact,
            bracket=None,
            faces=list(snapshot.faces),
        )

    def _prune_stale_snapshots(
        self, scene: list[Snapshot[TrackedFace[FaceEmbeddingT]]]
    ) -> None:
        # Snapshots behind the spline window can never be used again.
        drop = self._segment_index - self._lookahead_snapshots
        if drop > 0:
            del scene[:drop]
            self._segment_index -= drop

    async def _interpolate(
        self,
        scene: list[Snapshot[TrackedFace[FaceEmbeddingT]]],
        frame_index: FrameIndex,
    ) -> list[TrackedFace[FaceEmbeddingT]]:
        """Interpolate every face of the current segment's start snapshot,
        matching control points across snapshots by track id. The window never
        leaves the current scene."""
        segment_start = scene[self._segment_index]

        window_start = max(0, self._segment_index - self._lookahead_snapshots)
        window_end = min(
            len(scene), self._segment_index + self._lookahead_snapshots + 2
        )
        window = scene[window_start:window_end]
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
            results.append(
                await self._interpolator.interpolate(slots, frame_index - span_start)
            )
        return results
