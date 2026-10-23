from pathlib import Path

import click

from ...pipeline import run as run_pipeline
from ...transforms import (
    DEFAULT_RADIUS_RATIO,
    DrawCircleWithONNX,
    DrawCircleWithoutAnything,
    DrawCircleWithSafeTensors,
    DrawCircleWithTensorRT,
)
from .options import BackendType, backend_option, load_backend


@click.command()
@click.argument("input_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("output_path", type=click.Path(dir_okay=False, path_type=Path))
@backend_option
@click.option(
    "--radius",
    type=float,
    default=DEFAULT_RADIUS_RATIO,
    show_default=True,
    help="Circle radius, relative to the frame height.",
)
@click.option(
    "--onnx-model",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Draw the circle with this ONNX model (see `export-onnx`), run by ONNX Runtime.",
)
@click.option(
    "--safetensors-weights",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Draw the circle with these weights (see `export-safetensors`), loaded into the PyTorch model.",
)
@click.option(
    "--tensorrt-engine",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Draw the circle with this TensorRT engine (see `export-tensorrt`). GPU backend only.",
)
def run(
    input_path: Path,
    output_path: Path,
    backend_type: BackendType,
    radius: float,
    onnx_model: Path | None,
    safetensors_weights: Path | None,
    tensorrt_engine: Path | None,
) -> None:
    """Process INPUT_PATH, and write the result to OUTPUT_PATH."""
    models = {
        "--onnx-model": onnx_model,
        "--safetensors-weights": safetensors_weights,
        "--tensorrt-engine": tensorrt_engine,
    }
    given = [option for option, value in models.items() if value is not None]
    if len(given) > 1:
        raise click.UsageError(f"{' and '.join(given)} are mutually exclusive")
    if tensorrt_engine is not None and backend_type == "cpu":
        raise click.UsageError("--tensorrt-engine needs --backend=gpu: TensorRT only runs on the GPU")

    backend = load_backend(backend_type)

    if onnx_model is not None:
        settings = DrawCircleWithONNX.Settings(model_path=onnx_model, radius_ratio=radius)
        run_pipeline(backend, DrawCircleWithONNX, settings, input_path, output_path)
    elif safetensors_weights is not None:
        settings = DrawCircleWithSafeTensors.Settings(weights_path=safetensors_weights, radius_ratio=radius)
        run_pipeline(backend, DrawCircleWithSafeTensors, settings, input_path, output_path)
    elif tensorrt_engine is not None:
        settings = DrawCircleWithTensorRT.Settings(engine_path=tensorrt_engine, radius_ratio=radius)
        run_pipeline(backend, DrawCircleWithTensorRT, settings, input_path, output_path)
    else:
        settings = DrawCircleWithoutAnything.Settings(radius_ratio=radius)
        run_pipeline(backend, DrawCircleWithoutAnything, settings, input_path, output_path)
