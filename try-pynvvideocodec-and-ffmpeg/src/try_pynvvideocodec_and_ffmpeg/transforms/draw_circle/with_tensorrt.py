"""The circle, compiled by TensorRT for this very GPU.

TensorRT takes the ONNX model and builds an "engine": the same computation,
optimized for one GPU model and one TensorRT version (an engine built here won't
run elsewhere). `export_tensorrt` builds it once, `WithTensorRT` runs it straight
on the PyTorch tensors' GPU memory, on PyTorch's own CUDA stream. GPU only.
"""

import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import structlog

# `tensorrt` only re-exports this compiled module (`from tensorrt_bindings import *`),
# which type checkers can't see through: importing it directly makes it one
# untyped module instead of an error on every `trt.` attribute
import tensorrt_bindings.tensorrt as trt  # ty: ignore[unresolved-import]
import torch

from ...core import VideoInfo
from .model import DEFAULT_COLOR, Color, Model
from .with_onnx import FRAME_INPUT, OUTPUT_NAME, RADIUS_RATIO_INPUT, export_model_to_onnx
from .without_anything import DEFAULT_RADIUS_RATIO

log = structlog.get_logger()

# Frame sizes the engine accepts: TensorRT optimizes for `OPTIMAL`, and rejects
# anything outside [MIN, MAX]
MIN_FRAME_SHAPE = (64, 64, 3)
OPTIMAL_FRAME_SHAPE = (1080, 1920, 3)
MAX_FRAME_SHAPE = (2160, 3840, 3)


class TensorRTModel(Model):
    """`Model`, with its `where` in int32: TensorRT doesn't support it on uint8.

    Same values (0-255 fits in int32), so the output is bit-identical.
    """

    def forward(self, frame: torch.Tensor, radius_ratio: torch.Tensor) -> torch.Tensor:
        mask = self.circle_mask(frame, radius_ratio)
        return torch.where(mask, self.color.to(torch.int32), frame.to(torch.int32)).to(torch.uint8)


def export_tensorrt(path: Path, color: Color = DEFAULT_COLOR) -> None:
    logger = trt.Logger(trt.Logger.WARNING)
    with tempfile.TemporaryDirectory() as directory:
        onnx_path = Path(directory) / "model.onnx"
        export_model_to_onnx(TensorRTModel(color), onnx_path)
        builder = trt.Builder(logger)
        network = builder.create_network(0)
        parser = trt.OnnxParser(network, logger)
        if not parser.parse(onnx_path.read_bytes()):
            errors = [parser.get_error(index).desc() for index in range(parser.num_errors)]
            raise RuntimeError(f"TensorRT couldn't parse the ONNX model: {errors}")

    config = builder.create_builder_config()
    profile = builder.create_optimization_profile()
    profile.set_shape(FRAME_INPUT, MIN_FRAME_SHAPE, OPTIMAL_FRAME_SHAPE, MAX_FRAME_SHAPE)
    config.add_optimization_profile(profile)
    engine = builder.build_serialized_network(network, config)
    if engine is None:
        raise RuntimeError("TensorRT couldn't build the engine (see its logs above)")
    path.write_bytes(engine)


class WithTensorRT:
    """Draw the circle by running an engine built with `export_tensorrt()`."""

    @dataclass(frozen=True)
    class Settings:
        engine_path: Path
        radius_ratio: float = DEFAULT_RADIUS_RATIO  # Relative to the frame height

    def __init__(
        self,
        engine: trt.ICudaEngine,
        context: trt.IExecutionContext,
        radius_ratio: torch.Tensor,
        stream: torch.cuda.Stream,
    ):
        self.engine = engine  # The context needs its engine alive
        self.context = context
        self.radius_ratio = radius_ratio  # 0-d float32 tensor, on the GPU
        self.stream = stream

    @staticmethod
    @contextmanager
    def open(info: VideoInfo, device: torch.device, settings: "WithTensorRT.Settings") -> Generator["WithTensorRT"]:
        if device.type != "cuda":
            raise ValueError("TensorRT only runs on the GPU: use the GPU backend")
        runtime = trt.Runtime(trt.Logger(trt.Logger.WARNING))
        engine = runtime.deserialize_cuda_engine(settings.engine_path.read_bytes())
        if engine is None:
            raise RuntimeError(
                f"Couldn't load {settings.engine_path}: engines only run on the GPU and TensorRT version that built them"
            )
        context = engine.create_execution_context()
        # All frames of a video have the same size: set it once
        shape = (info.height, info.width, 3)
        if not context.set_input_shape(FRAME_INPUT, shape):
            raise ValueError(f"The engine accepts frames from {MIN_FRAME_SHAPE} to {MAX_FRAME_SHAPE}, not {shape}")
        tensors = [engine.get_tensor_name(index) for index in range(engine.num_io_tensors)]
        log.info("tensorrt.engine", path=str(settings.engine_path), tensors=tensors)
        log.debug(
            "tensorrt.tensors",
            tensors={name: (str(engine.get_tensor_mode(name)), str(engine.get_tensor_dtype(name))) for name in tensors},
        )
        radius_ratio = torch.tensor(settings.radius_ratio, dtype=torch.float32, device=device)
        context.set_tensor_address(RADIUS_RATIO_INPUT, radius_ratio.data_ptr())
        # TensorRT's own stream: on CUDA's legacy default stream (PyTorch's, unless
        # told otherwise), which implicitly syncs with every other stream,
        # TensorRT adds `cudaStreamSynchronize()` calls to stay correct, and warns
        stream = torch.cuda.Stream(device=device)
        yield WithTensorRT(engine, context, radius_ratio, stream)

    def transform_frame(self, frame: torch.Tensor) -> torch.Tensor:
        frame = frame.contiguous()
        output = torch.empty_like(frame)
        # Like ONNX Runtime's IO binding: raw device addresses, nothing is copied
        self.context.set_tensor_address(FRAME_INPUT, frame.data_ptr())
        self.context.set_tensor_address(OUTPUT_NAME, output.data_ptr())
        # Ordered with PyTorch on the GPU, without blocking the CPU: TensorRT's stream
        # waits for the frame to be ready, then PyTorch's stream waits for the output
        current = torch.cuda.current_stream()
        self.stream.wait_stream(current)
        if not self.context.execute_async_v3(self.stream.cuda_stream):
            raise RuntimeError("TensorRT failed to run the engine")
        current.wait_stream(self.stream)
        # Allocated on PyTorch's stream, written on TensorRT's: tell the caching
        # allocator, so that the memory isn't reused before TensorRT is done with it
        output.record_stream(self.stream)
        return output
