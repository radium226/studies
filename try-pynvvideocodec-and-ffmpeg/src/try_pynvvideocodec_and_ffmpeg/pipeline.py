import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import AbstractContextManager, ExitStack
from pathlib import Path

import structlog
import torch

from .core import Backend, BatchFrameTransformer, FrameTransformer, VideoInfo
from .monitoring import PCIeMonitor

log = structlog.get_logger()


def run[S](
    backend_type: type[Backend],
    transformer_type: type[FrameTransformer[S]],
    settings: S,
    input_path: Path,
    output_path: Path,
) -> None:
    """Transform the frames one at a time."""
    log.debug("pipeline", backend=backend_type.__name__, transformer=transformer_type.__name__, settings=settings)

    def transform(frame_transformer: FrameTransformer[S], frames: Iterable[torch.Tensor]) -> Iterator[torch.Tensor]:
        for index, frame in enumerate(frames):
            transformed = frame_transformer.transform_frame(frame)
            if index == 0:
                log.debug(
                    "frame.first",
                    input_data_ptr=hex(frame.data_ptr()),
                    output_data_ptr=hex(transformed.data_ptr()),
                    shape=tuple(frame.shape),
                    dtype=str(frame.dtype),
                    device=str(frame.device),
                )
            yield transformed

    _run(
        backend_type,
        lambda info, device: transformer_type.open(info, device, settings),
        transform,
        input_path,
        output_path,
    )


def run_batched[S](
    backend_type: type[Backend],
    transformer_type: type[BatchFrameTransformer[S]],
    settings: S,
    input_path: Path,
    output_path: Path,
    batch_size: int,
) -> None:
    """Transform the frames `batch_size` at a time, stacked in one tensor."""
    log.debug(
        "pipeline",
        backend=backend_type.__name__,
        transformer=transformer_type.__name__,
        settings=settings,
        batch_size=batch_size,
    )

    def transform(
        frame_transformer: BatchFrameTransformer[S], frames: Iterable[torch.Tensor]
    ) -> Iterator[torch.Tensor]:
        for index, batch in enumerate(batch_frames(frames, batch_size)):
            transformed = frame_transformer.transform_frames(batch)
            if index == 0:
                log.debug(
                    "batch.first",
                    input_data_ptr=hex(batch.data_ptr()),
                    output_data_ptr=hex(transformed.data_ptr()),
                    shape=tuple(batch.shape),
                    dtype=str(batch.dtype),
                    device=str(batch.device),
                )
            # Iterating a tensor yields views along its first dimension: one per frame
            yield from transformed

    _run(
        backend_type,
        lambda info, device: transformer_type.open(info, device, settings),
        transform,
        input_path,
        output_path,
    )


def batch_frames(frames: Iterable[torch.Tensor], batch_size: int) -> Iterator[torch.Tensor]:
    """Stack frames into `(N, H, W, 3)` batches of `batch_size` (the last one may be shorter).

    The frames are copied into one buffer, reused for every batch: a batch is
    only valid until the next one is requested.
    """
    buffer: torch.Tensor | None = None
    count = 0
    for frame in frames:
        if buffer is None:
            buffer = frame.new_empty((batch_size, *frame.shape))
            log.debug("batch.buffer", shape=tuple(buffer.shape), bytes=buffer.nbytes, device=str(buffer.device))
        buffer[count].copy_(frame)
        if frame.is_cuda:
            # A source's frame is only valid until the next one is requested (the
            # decoder recycles its surfaces), and on the GPU, the copy is asynchronous
            torch.cuda.current_stream(frame.device).synchronize()
        count += 1
        if count == batch_size:
            yield buffer
            count = 0
    if buffer is not None and count > 0:
        yield buffer[:count]


def _run[T](
    backend_type: type[Backend],
    open_transformer: Callable[[VideoInfo, torch.device], AbstractContextManager[T]],
    transform: Callable[[T, Iterable[torch.Tensor]], Iterator[torch.Tensor]],
    input_path: Path,
    output_path: Path,
) -> None:
    # Everything entered here is exited in reverse order: sink, monitor, transformer, source, backend
    with ExitStack() as stack:
        backend = stack.enter_context(backend_type.create())
        source = stack.enter_context(backend.open_source(input_path))
        info = source.info
        log.info(
            "input", path=str(input_path), width=info.width, height=info.height, fps=str(info.fps), codec=info.codec
        )

        # Opened before the timer starts: loading a model is not per-frame work
        frame_transformer = stack.enter_context(open_transformer(info, backend.device))
        stack.enter_context(PCIeMonitor.monitor_pcie())

        # The sink gets its own stack, closed right after the loop: finalizing the
        # file is part of the work, so it must happen before the timer stops (and
        # before the monitor stops). On error, the outer stack still closes it.
        sink_stack = stack.enter_context(ExitStack())
        sink = sink_stack.enter_context(backend.open_sink(output_path, info))

        frame_count = 0
        start = time.perf_counter()
        for transformed in transform(frame_transformer, source):
            sink.write(transformed)
            frame_count += 1
            if frame_count % 100 == 0:
                log.debug("frame.progress", frames=frame_count, seconds=round(time.perf_counter() - start, 2))
        sink_stack.close()
        elapsed = time.perf_counter() - start

    log.info(
        "output",
        path=str(output_path),
        frames=frame_count,
        seconds=round(elapsed, 2),
        fps=round(frame_count / elapsed),
    )
