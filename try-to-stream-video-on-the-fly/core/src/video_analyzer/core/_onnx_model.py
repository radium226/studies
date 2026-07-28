from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Self

if TYPE_CHECKING:
    import onnxruntime as ort


class OnnxModel:
    """Shared ONNX session lifecycle: the session only exists between
    __enter__ and __exit__, so model weights are loaded lazily and released
    deterministically."""

    def __init__(self, model_path: Path) -> None:
        self.model_path = model_path
        self._session: ort.InferenceSession | None = None

    def __enter__(self) -> Self:
        import onnxruntime as ort

        self._session = ort.InferenceSession(
            str(self.model_path), providers=["CPUExecutionProvider"]
        )
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self._session = None

    def _require_session(self) -> "ort.InferenceSession":
        if self._session is None:
            raise RuntimeError(
                f"{type(self).__name__} not open — use as a context manager"
            )
        return self._session
