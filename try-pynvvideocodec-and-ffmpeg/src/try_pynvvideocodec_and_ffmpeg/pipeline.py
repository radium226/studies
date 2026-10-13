import time
from contextlib import ExitStack
from pathlib import Path

import structlog

from .core import Backend, FrameTransformer
from .monitoring import PCIeMonitor

log = structlog.get_logger()


def run[S](
    backend_type: type[Backend],
    transformer_type: type[FrameTransformer[S]],
    settings: S,
    input_path: Path,
    output_path: Path,
) -> None:
    log.debug("pipeline", backend=backend_type.__name__, transformer=transformer_type.__name__, settings=settings)
    # Everything entered here is exited in reverse order: sink, monitor, transformer, source, backend
    with ExitStack() as stack:
        backend = stack.enter_context(backend_type.create())
        source = stack.enter_context(backend.open_source(input_path))
        info = source.info
        log.info(
            "input", path=str(input_path), width=info.width, height=info.height, fps=str(info.fps), codec=info.codec
        )

        # Opened before the timer starts: loading a model is not per-frame work
        frame_transformer = stack.enter_context(transformer_type.open(info, backend.device, settings))
        stack.enter_context(PCIeMonitor.monitor_pcie())

        # The sink gets its own stack, closed right after the loop: finalizing the
        # file is part of the work, so it must happen before the timer stops (and
        # before the monitor stops). On error, the outer stack still closes it.
        sink_stack = stack.enter_context(ExitStack())
        sink = sink_stack.enter_context(backend.open_sink(output_path, info))

        frame_count = 0
        start = time.perf_counter()
        for frame in source:
            transformed = frame_transformer.transform_frame(frame)
            if frame_count == 0:
                log.debug(
                    "frame.first",
                    input_data_ptr=hex(frame.data_ptr()),
                    output_data_ptr=hex(transformed.data_ptr()),
                    shape=tuple(frame.shape),
                    dtype=str(frame.dtype),
                    device=str(frame.device),
                )
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
