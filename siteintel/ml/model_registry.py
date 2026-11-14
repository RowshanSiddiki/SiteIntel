import logging
import hashlib
import pickle
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from threading import RLock
from typing import Any


MODEL_DIRECTORY = Path(__file__).resolve().parents[2] / "pkl_files"
MODEL_SPECS = {
    "cement_demand": {
        "model": "cement_model.pkl",
        "features": "cement_features.pkl",
        "metadata": "cement_metadata.pkl",
        "estimator": "RandomForestRegressor",
        "target": "consumed_tonnes",
        "expected_features": (
            "planned_pour_tonnes",
            "site_rolling_mean_30",
            "rain_mm",
            "avg_temp_c",
        ),
    },
    "concrete_strength": {
        "model": "concrete_model.pkl",
        "features": "concrete_features.pkl",
        "metadata": "concrete_metadata.pkl",
        "estimator": "GradientBoostingRegressor",
        "target": "compressive_strength_mpa",
        "expected_features": ("cement", "slag", "water", "age"),
    },
    "delay_risk": {
        "model": "delay_risk_model.pkl",
        "features": "delay_risk_features.pkl",
        "metadata": "delay_risk_metadata.pkl",
        "encoders": "delay_risk_encoders.pkl",
        "estimator": "GradientBoostingClassifier",
        "target": "Delay_Risk",
        "expected_features": (
            "Material_Availability",
            "Equipment_Availability",
            "Weather_Risk",
            "Supplier_Delay",
        ),
    },
}
DELAY_FEATURE_ENCODERS = {
    "Material_Availability": {"Low": 0, "Medium": 1, "High": 2},
    "Equipment_Availability": {"Low": 0, "Medium": 1, "High": 2},
    "Weather_Risk": {"Low": 0, "Medium": 1, "High": 2},
    "Supplier_Delay": {"No": 0, "Yes": 1},
}
logger = logging.getLogger(__name__)


class ModelArtifactError(RuntimeError):
    """Raised when a configured model package is missing or incompatible."""


@dataclass(frozen=True)
class ModelPackage:
    key: str
    model: Any
    features: tuple[str, ...]
    metadata: dict[str, Any]
    encoders: dict[str, dict[str, int]] | None = None


class ModelRegistry:
    def __init__(self, directory: Path = MODEL_DIRECTORY) -> None:
        self.directory = directory.resolve()
        self._packages: dict[str, ModelPackage] = {}
        self._versions: dict[str, str] = {}
        self._lock = RLock()

    def artifact_path(self, model_key: str, artifact_type: str) -> Path:
        try:
            filename = MODEL_SPECS[model_key][artifact_type]
        except KeyError as error:
            raise ValueError("Unknown model artifact.") from error
        return self.directory / filename

    def get(self, model_key: str) -> ModelPackage:
        if model_key not in MODEL_SPECS:
            raise ValueError("Unknown model.")
        with self._lock:
            if model_key not in self._packages:
                self._packages[model_key] = self._load_package(model_key)
            return self._packages[model_key]

    def version(self, model_key: str) -> str:
        with self._lock:
            if model_key not in self._versions:
                digest = hashlib.sha256()
                types = ("model", "features", "encoders") if model_key == "delay_risk" else ("model", "features")
                for artifact_type in types:
                    path = self.artifact_path(model_key, artifact_type)
                    if not path.is_file():
                        raise ModelArtifactError("A model artifact is missing.")
                    digest.update(artifact_type.encode())
                    with path.open("rb") as artifact:
                        for chunk in iter(lambda: artifact.read(1024 * 1024), b""):
                            digest.update(chunk)
                self._versions[model_key] = digest.hexdigest()
            return self._versions[model_key]

    def _load_pickle(self, path: Path) -> Any:
        if not path.is_file():
            raise ModelArtifactError(f"Required model artifact is missing: {path.name}")
        try:
            with path.open("rb") as artifact:
                return pickle.load(artifact)
        except (OSError, EOFError, pickle.UnpicklingError, ImportError, AttributeError) as error:
            logger.exception("Could not load model artifact %s", path.name)
            raise ModelArtifactError(
                f"Required model artifact could not be loaded: {path.name}"
            ) from error

    def _load_package(self, model_key: str) -> ModelPackage:
        spec = MODEL_SPECS[model_key]
        model = self._load_pickle(self.artifact_path(model_key, "model"))
        raw_features = self._load_pickle(self.artifact_path(model_key, "features"))
        metadata = self._load_pickle(self.artifact_path(model_key, "metadata"))

        if not isinstance(raw_features, list) or not raw_features or not all(
            isinstance(feature, str) for feature in raw_features
        ):
            raise ModelArtifactError("Saved model features are invalid.")
        if len(set(raw_features)) != len(raw_features):
            raise ModelArtifactError("Saved model features contain duplicates.")
        if tuple(raw_features) != spec["expected_features"]:
            raise ModelArtifactError("Saved features do not match the model specification.")
        if not isinstance(metadata, dict) or metadata.get("features") != raw_features:
            raise ModelArtifactError("Model metadata does not match its saved features.")
        if metadata.get("target") != spec["target"]:
            raise ModelArtifactError("Model target does not match its specification.")
        if type(model).__name__ != spec["estimator"]:
            raise ModelArtifactError("Loaded model type does not match its specification.")

        model_features = getattr(model, "feature_names_in_", None)
        if model_features is not None and list(model_features) != raw_features:
            raise ModelArtifactError("Model feature order differs from its saved features.")
        if getattr(model, "n_features_in_", None) != len(raw_features):
            raise ModelArtifactError("Model feature count differs from its saved features.")
        if not callable(getattr(model, "predict", None)):
            raise ModelArtifactError("Loaded model has no callable prediction method.")

        encoders = None
        if model_key == "delay_risk":
            raw_encoders = self._load_pickle(
                self.artifact_path(model_key, "encoders")
            )
            encoders = (
                raw_encoders.get("feature_encoders")
                if isinstance(raw_encoders, dict)
                else None
            )
            if encoders != DELAY_FEATURE_ENCODERS:
                raise ModelArtifactError("Delay-risk encoders do not match the specification.")
            if not callable(getattr(model, "predict_proba", None)):
                raise ModelArtifactError("Delay-risk model has no probability method.")
            if 1 not in getattr(model, "classes_", ()):
                raise ModelArtifactError("Delay-risk model does not define positive class 1.")

        return ModelPackage(
            key=model_key,
            model=model,
            features=tuple(raw_features),
            metadata=metadata,
            encoders=encoders,
        )


@lru_cache(maxsize=1)
def load_model_registry() -> ModelRegistry:
    return ModelRegistry()
