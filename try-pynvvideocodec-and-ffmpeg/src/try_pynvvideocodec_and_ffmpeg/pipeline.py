import time
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
    with backend_type.create() as backend, backend.open_source(input_path) as source:
        info = source.info
        log.info(
            "input", path=str(input_path), width=info.width, height=info.height, fps=str(info.fps), codec=info.codec
        )

        # Opened before the timer starts: loading a model is not per-frame work
        with (
            transformer_type.open(info, backend.device, settings) as frame_transformer,
            PCIeMonitor.monitor_pcie(),
        ):
            with backend.open_sink(output_path, info) as sink:
                frame_count = 0
                start = time.perf_counter()
                for frame in source:
                    sink.write(frame_transformer.transform_frame(frame))
                    frame_count += 1
            # Measured once the sink is finalized, but before stopping the monitor
            elapsed = time.perf_counter() - start

        log.info(
            "output",
            path=str(output_path),
            frames=frame_count,
            seconds=round(elapsed, 2),
            fps=round(frame_count / elapsed),
        )
