from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import Any, Callable


MODEL_DIRECTORY = Path(__file__).resolve().parents[2] / "pkl_files"
MODEL_FILES = {
    "cement_demand": "cement_demand.pkl",
    "budget_overrun": "budget_overrun.pkl",
    "construction_delay": "construction_delay.pkl",
}


class ModelRegistry:
    def __init__(self, directory: Path = MODEL_DIRECTORY) -> None:
        self.directory = directory.resolve()
        self._models: dict[str, Any] = {}
        self._lock = Lock()

    def model_path(self, model_key: str) -> Path:
        try:
            filename = MODEL_FILES[model_key]
        except KeyError as error:
            raise ValueError(f"Unknown model: {model_key}") from error
        return self.directory / filename

    def load(self, model_key: str, loader: Callable[[Path], Any]) -> Any:
        with self._lock:
            if model_key not in self._models:
                path = self.model_path(model_key)
                if not path.is_file():
                    raise FileNotFoundError(f"Model artifact not found: {path}")
                self._models[model_key] = loader(path)
            return self._models[model_key]


@lru_cache(maxsize=1)
def load_model_registry() -> ModelRegistry:
    return ModelRegistry()
