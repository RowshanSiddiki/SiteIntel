import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from numbers import Real
from pathlib import Path
from typing import Any

import joblib
import pandas as pd


logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
MODEL_DIRECTORY = PROJECT_ROOT / "pkl_files"

_MODEL_FILES = {
    "workforce_attendance": "workforce_attendance_model.pkl",
    "cement_demand": "cement_demand_model.pkl",
    "supply_chain": "supply_chain_model.pkl",
    "construction_cost": "construction_estimate_model.pkl",
    "civil_risk": "civil_risk_model.pkl",
}


class ModelError(RuntimeError):
    """Base error for model loading and inference failures."""


class ModelNotFoundError(ModelError):
    """A configured model artifact does not exist."""


class ModelLoadError(ModelError):
    """A model or its metadata could not be loaded or verified."""


class InvalidPredictionInput(ValueError):
    """Prediction features do not match the model's validated input schema."""


class PredictionError(ModelError):
    """The loaded model could not produce a valid prediction."""


@dataclass(frozen=True)
class ModelArtifact:
    key: str
    filename: str
    model: Any
    target: str
    input_features: tuple[str, ...]
    numeric_features: frozenset[str]
    categorical_features: frozenset[str]


def _metadata_features(metadata: Any) -> tuple[str, ...]:
    if not isinstance(metadata, dict):
        raise ModelLoadError("Model metadata must be a dictionary.")

    features = metadata.get("features", metadata.get("feature_columns"))
    if (
        not isinstance(features, (list, tuple))
        or not features
        or any(not isinstance(feature, str) or not feature for feature in features)
        or len(set(features)) != len(features)
    ):
        raise ModelLoadError("Model metadata does not contain a valid feature list.")
    return tuple(features)


def _feature_types(
    model: Any, feature_names: tuple[str, ...]
) -> tuple[frozenset[str], frozenset[str]]:
    try:
        preprocessor = model.named_steps["preprocessor"]
        transformers = preprocessor.transformers_
    except (AttributeError, KeyError, TypeError) as error:
        raise ModelLoadError("Model has no fitted preprocessing pipeline.") from error

    numeric: set[str] = set()
    categorical: set[str] = set()
    for name, _transformer, columns in transformers:
        if not isinstance(columns, (list, tuple)):
            raise ModelLoadError("Model preprocessing feature selectors are unsupported.")
        if any(not isinstance(column, str) for column in columns):
            raise ModelLoadError("Model preprocessing selectors must use feature names.")
        if name == "numeric":
            numeric.update(columns)
        elif name == "categorical":
            categorical.update(columns)

    if numeric & categorical or numeric | categorical != set(feature_names):
        raise ModelLoadError("Preprocessing feature selectors do not match metadata.")
    return frozenset(numeric), frozenset(categorical)


@lru_cache(maxsize=len(_MODEL_FILES))
def load_model(key: str) -> ModelArtifact:
    filename = _MODEL_FILES.get(key)
    if filename is None:
        raise ModelNotFoundError(f"No model is configured for '{key}'.")

    model_path = MODEL_DIRECTORY / filename
    metadata_path = MODEL_DIRECTORY / f"{Path(filename).stem}_metadata.pkl"
    if not model_path.is_file():
        raise ModelNotFoundError(f"Model file is missing: {filename}.")
    if not metadata_path.is_file():
        raise ModelNotFoundError(f"Model metadata file is missing for: {filename}.")

    try:
        metadata = joblib.load(metadata_path)
        model = joblib.load(model_path)
        feature_names = _metadata_features(metadata)
        target = metadata.get("target")
        if not isinstance(target, str) or not target:
            raise ModelLoadError("Model metadata does not contain a target name.")

        model_features = getattr(model, "feature_names_in_", None)
        if model_features is not None and tuple(model_features) != feature_names:
            raise ModelLoadError(
                "Model feature order does not match its saved metadata."
            )
        if not callable(getattr(model, "predict", None)):
            raise ModelLoadError("Loaded artifact does not provide prediction.")

        numeric, categorical = _feature_types(model, feature_names)
        return ModelArtifact(
            key=key,
            filename=filename,
            model=model,
            target=target,
            input_features=feature_names,
            numeric_features=numeric,
            categorical_features=categorical,
        )
    except ModelError:
        raise
    except Exception as error:
        logger.exception("Could not load or inspect model %s", filename)
        raise ModelLoadError(f"Unable to load model: {filename}.") from error


def _normalise_scalar(value: Any, feature: str) -> Any:
    if hasattr(value, "item") and callable(value.item):
        value = value.item()

    if value is None:
        raise InvalidPredictionInput(f"Feature '{feature}' cannot be null.")
    if isinstance(value, bool):
        return value
    if isinstance(value, Real):
        try:
            numeric_value = float(value)
        except (OverflowError, TypeError, ValueError) as error:
            raise InvalidPredictionInput(
                f"Feature '{feature}' must be finite."
            ) from error
        if not math.isfinite(numeric_value):
            raise InvalidPredictionInput(f"Feature '{feature}' must be finite.")
        return value
    if isinstance(value, str):
        if not value.strip():
            raise InvalidPredictionInput(f"Feature '{feature}' cannot be blank.")
        return value
    raise InvalidPredictionInput(
        f"Feature '{feature}' must be a string or finite number."
    )


def _validated_frame(artifact: ModelArtifact, features: Mapping[str, Any]) -> pd.DataFrame:
    if not isinstance(features, Mapping):
        raise InvalidPredictionInput("Prediction input must be a feature mapping.")
    if any(not isinstance(name, str) for name in features):
        raise InvalidPredictionInput("All feature names must be strings.")

    expected = set(artifact.input_features)
    supplied = set(features)
    missing = [name for name in artifact.input_features if name not in supplied]
    unexpected = sorted(supplied - expected)
    if missing or unexpected:
        details = []
        if missing:
            details.append(f"missing features: {', '.join(missing)}")
        if unexpected:
            details.append(f"unexpected features: {', '.join(unexpected)}")
        raise InvalidPredictionInput("; ".join(details))

    row: dict[str, Any] = {}
    for name in artifact.input_features:
        value = _normalise_scalar(features[name], name)
        if name in artifact.numeric_features and (
            not isinstance(value, Real) or isinstance(value, bool)
        ):
            raise InvalidPredictionInput(f"Feature '{name}' must be numeric.")
        row[name] = value
    return pd.DataFrame([row], columns=artifact.input_features)


def _python_scalar(value: Any) -> Any:
    return value.item() if hasattr(value, "item") and callable(value.item) else value


def predict(key: str, features: Mapping[str, Any]) -> dict[str, Any]:
    artifact = load_model(key)
    frame = _validated_frame(artifact, features)
    try:
        prediction = _python_scalar(artifact.model.predict(frame)[0])
        if isinstance(prediction, Real) and not math.isfinite(float(prediction)):
            raise PredictionError("Model returned a non-finite prediction.")
        result: dict[str, Any] = {
            "model": key,
            "target": artifact.target,
            "prediction": prediction,
            "probabilities": None,
        }
        predict_proba = getattr(artifact.model, "predict_proba", None)
        if callable(predict_proba):
            classes = getattr(artifact.model, "classes_", None)
            if classes is None:
                raise PredictionError("Classifier has no class labels.")
            probabilities = predict_proba(frame)[0]
            if len(classes) != len(probabilities) or any(
                not math.isfinite(float(probability))
                or not 0.0 <= float(probability) <= 1.0
                for probability in probabilities
            ):
                raise PredictionError("Classifier returned invalid probabilities.")
            result["probabilities"] = [
                {
                    "class": _python_scalar(label),
                    "probability": float(probability),
                }
                for label, probability in zip(classes, probabilities, strict=True)
            ]
        return result
    except PredictionError:
        raise
    except Exception as error:
        logger.exception("Prediction failed for model %s", key)
        raise PredictionError(f"Prediction failed for model '{key}'.") from error
