import pytest

from video_analyzer import core, kernel


class _FakeClock(kernel.Clock):
    """Hand-advanced monotonic clock, so window eviction and pass durations are
    exact instead of dependent on how fast the test machine runs."""

    def __init__(self) -> None:
        self.time = 0.0

    def now(self) -> float:
        return self.time

    def advance(self, seconds: float) -> None:
        self.time += seconds


def _detection(x: float = 0.0) -> kernel.Detection:
    return kernel.Detection(
        bounding_box=kernel.BoundingBox(x=x, y=0.0, width=10.0, height=10.0),
        landmarks=kernel.FaceLandmarks(
            left_eye=(0.0, 0.0),
            right_eye=(0.0, 0.0),
            nose=(0.0, 0.0),
            mouth_left=(0.0, 0.0),
            mouth_right=(0.0, 0.0),
        ),
        confidence=1.0,
    )


def _annotated_frame(
    frame_index: int, track_count: int
) -> kernel.AnnotatedFrame[int, kernel.TrackedFace[int]]:
    return kernel.AnnotatedFrame(
        frame=kernel.Frame(index=frame_index, content=0, is_scene_start=False),
        faces=[
            kernel.TrackedFace(
                track_id=str(track_index),
                face=kernel.Face(detection=_detection(), embedding=0),
            )
            for track_index in range(track_count)
        ],
        interpolation_bracket=None,
        is_exact=False,
    )


class _SlowFaceDetector(kernel.FaceDetector[int]):
    """Charges `duration_s` to the clock per pass, so the decorator has a real
    elapsed time to measure."""

    def __init__(
        self, clock: _FakeClock, duration_s: float, faces_per_frame: list[int]
    ) -> None:
        self._clock = clock
        self._duration_s = duration_s
        self._faces_per_frame = faces_per_frame

    async def detect_faces(
        self, frame_batch: list[kernel.Frame[int]]
    ) -> list[list[kernel.Face[None]]]:
        self._clock.advance(self._duration_s)
        return [
            [kernel.Face(detection=_detection(), embedding=None) for _ in range(count)]
            for count in self._faces_per_frame
        ]


class _SlowFaceEmbedder(kernel.FaceEmbedder[int, int]):
    def __init__(self, clock: _FakeClock, duration_s: float) -> None:
        self._clock = clock
        self._duration_s = duration_s

    async def embed_faces(
        self, face_batch: list[tuple[kernel.Frame[int], kernel.Face[None]]]
    ) -> list[kernel.Face[int]]:
        self._clock.advance(self._duration_s)
        return [
            kernel.Face(detection=frame_face[1].detection, embedding=0)
            for frame_face in face_batch
        ]


class _RecordingBroadcaster(kernel.FrameBroadcaster[int, kernel.TrackedFace[int]]):
    def __init__(self) -> None:
        self.broadcast_frames: list[
            kernel.AnnotatedFrame[int, kernel.TrackedFace[int]]
        ] = []

    async def broadcast_frame(
        self, annotated_frame: kernel.AnnotatedFrame[int, kernel.TrackedFace[int]]
    ) -> None:
        self.broadcast_frames.append(annotated_frame)


def test_window_s_must_be_positive() -> None:
    with pytest.raises(kernel.ConfigError):
        core.MetricsCollectorConfig(window_s=0.0)


def test_snapshot_is_all_zeroes_before_anything_is_recorded() -> None:
    snapshot = core.MetricsCollector(_FakeClock()).snapshot()

    assert snapshot["detection_ms"] == 0.0
    assert snapshot["processed_fps"] == 0.0
    assert snapshot["active_tracks"] == 0
    # A zero detection rate must not divide by zero on the way out.
    assert snapshot["detection_stride"] == 0.0
    assert snapshot["window_s"] == 5.0


async def test_metered_detector_times_the_pass_and_records_its_batch() -> None:
    clock = _FakeClock()
    metrics = core.MetricsCollector(clock)
    detector = core.MeteredFaceDetector(
        _SlowFaceDetector(clock, 0.2, [2, 0, 1]), metrics, clock
    )

    faces_per_frame = await detector.detect_faces(
        [kernel.Frame(index=i, content=0, is_scene_start=False) for i in range(3)]
    )

    assert [len(faces) for faces in faces_per_frame] == [2, 0, 1]
    snapshot = metrics.snapshot()
    assert snapshot["detection_ms"] == 200.0
    assert snapshot["detect_batch_size"] == 3.0
    assert snapshot["detections_per_frame"] == 1.0
    assert snapshot["detection_hz"] == pytest.approx(1 / 5.0)


async def test_metered_embedder_records_input_crop_count() -> None:
    clock = _FakeClock()
    metrics = core.MetricsCollector(clock)
    embedder = core.MeteredFaceEmbedder(_SlowFaceEmbedder(clock, 0.05), metrics, clock)

    frame = kernel.Frame(index=0, content=0, is_scene_start=False)
    await embedder.embed_faces(
        [(frame, kernel.Face(detection=_detection(), embedding=None))] * 4
    )

    snapshot = metrics.snapshot()
    assert snapshot["embedding_ms"] == 50.0
    assert snapshot["embed_batch_size"] == 4.0


async def test_batch_ms_sums_detection_and_embedding() -> None:
    clock = _FakeClock()
    metrics = core.MetricsCollector(clock)
    detector = core.MeteredFaceDetector(_SlowFaceDetector(clock, 0.2, [1]), metrics, clock)
    embedder = core.MeteredFaceEmbedder(_SlowFaceEmbedder(clock, 0.05), metrics, clock)

    await detector.detect_faces([kernel.Frame(index=0, content=0, is_scene_start=False)])
    frame = kernel.Frame(index=0, content=0, is_scene_start=False)
    await embedder.embed_faces([(frame, kernel.Face(detection=_detection(), embedding=None))])

    assert metrics.snapshot()["batch_ms"] == 250.0


async def test_metered_broadcaster_forwards_every_frame_and_counts_tracks() -> None:
    clock = _FakeClock()
    metrics = core.MetricsCollector(clock)
    wrapped = _RecordingBroadcaster()
    broadcaster = core.MeteredFrameBroadcaster(wrapped, metrics)

    frames = [_annotated_frame(0, 2), _annotated_frame(1, 3)]
    for frame in frames:
        await broadcaster.broadcast_frame(frame)

    assert wrapped.broadcast_frames == frames
    snapshot = metrics.snapshot()
    # Rates are per window second, so 2 frames over the default 5 s window.
    assert snapshot["processed_fps"] == pytest.approx(2 / 5.0)
    # The *latest* frame's face count, not a running maximum.
    assert snapshot["active_tracks"] == 3


async def test_detection_stride_is_rendered_frames_per_detected_frame() -> None:
    clock = _FakeClock()
    metrics = core.MetricsCollector(clock)
    detector = core.MeteredFaceDetector(_SlowFaceDetector(clock, 0.0, [0, 0]), metrics, clock)
    broadcaster = core.MeteredFrameBroadcaster(_RecordingBroadcaster(), metrics)

    await detector.detect_faces(
        [kernel.Frame(index=i, content=0, is_scene_start=False) for i in range(2)]
    )
    for index in range(10):
        await broadcaster.broadcast_frame(_annotated_frame(index, 0))

    # 10 frames rendered for 2 frames detected: the interpolator covered 5
    # video frames per real detection.
    assert metrics.snapshot()["detection_stride"] == 5.0


async def test_samples_older_than_the_window_stop_counting() -> None:
    clock = _FakeClock()
    metrics = core.MetricsCollector(clock, config=core.MetricsCollectorConfig(window_s=1.0))
    broadcaster = core.MeteredFrameBroadcaster(_RecordingBroadcaster(), metrics)

    await broadcaster.broadcast_frame(_annotated_frame(0, 1))
    assert metrics.snapshot()["processed_fps"] == 1.0

    clock.advance(2.0)
    assert metrics.snapshot()["processed_fps"] == 0.0
    # active_tracks is a last-seen value, not a windowed one — it survives.
    assert metrics.snapshot()["active_tracks"] == 1
