import math
from numbers import Real
from typing import Any, Mapping

import pandas as pd

from siteintel.ml.model_registry import ModelRegistry, load_model_registry


CEMENT_FEATURES = (
    "planned_pour_tonnes",
    "site_rolling_mean_30",
    "rain_mm",
    "avg_temp_c",
)
CONCRETE_FEATURES = ("cement", "slag", "water", "age")
DELAY_FEATURES = (
    "Material_Availability",
    "Equipment_Availability",
    "Weather_Risk",
    "Supplier_Delay",
)


class PredictionInputError(ValueError):
    """Raised when a prediction request does not match a model's inputs."""


def _validate_fields(
    values: Mapping[str, Any],
    features: tuple[str, ...],
) -> dict[str, Any]:
    if not isinstance(values, Mapping):
        raise PredictionInputError("JSON request body must be an object.")
    missing = [feature for feature in features if feature not in values]
    unexpected = [feature for feature in values if feature not in features]
    if missing:
        raise PredictionInputError(f"Missing required field(s): {', '.join(missing)}.")
    if unexpected:
        raise PredictionInputError(f"Unexpected field(s): {', '.join(unexpected)}.")
    return {feature: values[feature] for feature in features}


def _numeric_frame(
    values: Mapping[str, Any],
    features: tuple[str, ...],
    *,
    nonnegative: frozenset[str],
) -> pd.DataFrame:
    ordered = _validate_fields(values, features)
    parsed: dict[str, float] = {}
    for feature in features:
        value = ordered[feature]
        if isinstance(value, bool) or not isinstance(value, Real):
            raise PredictionInputError(f"{feature} must be a number.")
        try:
            numeric_value = float(value)
        except (OverflowError, ValueError) as error:
            raise PredictionInputError(f"{feature} must be finite.") from error
        if not math.isfinite(numeric_value):
            raise PredictionInputError(f"{feature} must be finite.")
        if feature in nonnegative and numeric_value < 0:
            raise PredictionInputError(f"{feature} cannot be negative.")
        parsed[feature] = numeric_value
    return pd.DataFrame([[parsed[name] for name in features]], columns=features)


def _registry(registry: ModelRegistry | None) -> ModelRegistry:
    return registry or load_model_registry()


def predict_cement_demand(
    values: Mapping[str, Any],
    registry: ModelRegistry | None = None,
) -> dict[str, float]:
    frame = _numeric_frame(
        values,
        CEMENT_FEATURES,
        nonnegative=frozenset(CEMENT_FEATURES[:-1]),
    )
    package = _registry(registry).get("cement_demand")
    prediction = float(package.model.predict(frame)[0])
    if not math.isfinite(prediction):
        raise RuntimeError("Model returned a non-finite prediction.")
    return {"predicted_consumption_tonnes": prediction}


def predict_concrete_strength(
    values: Mapping[str, Any],
    registry: ModelRegistry | None = None,
) -> dict[str, float]:
    frame = _numeric_frame(
        values,
        CONCRETE_FEATURES,
        nonnegative=frozenset(CONCRETE_FEATURES),
    )
    package = _registry(registry).get("concrete_strength")
    prediction = float(package.model.predict(frame)[0])
    if not math.isfinite(prediction):
        raise RuntimeError("Model returned a non-finite prediction.")
    return {"compressive_strength_mpa": prediction}


def predict_delay_risk(
    values: Mapping[str, Any],
    threshold: float = 0.5,
    registry: ModelRegistry | None = None,
) -> dict[str, Any]:
    if isinstance(threshold, bool) or not isinstance(threshold, Real):
        raise ValueError("Delay-risk threshold must be a number between 0 and 1.")
    threshold = float(threshold)
    if not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError("Delay-risk threshold must be a number between 0 and 1.")

    ordered = _validate_fields(values, DELAY_FEATURES)
    package = _registry(registry).get("delay_risk")
    encoded: list[int] = []
    for feature in DELAY_FEATURES:
        value = ordered[feature]
        if not isinstance(value, str) or value not in package.encoders[feature]:
            allowed = ", ".join(package.encoders[feature])
            raise PredictionInputError(
                f"{feature} must be one of: {allowed}."
            )
        encoded.append(package.encoders[feature][value])

    frame = pd.DataFrame([encoded], columns=package.features)
    probabilities = package.model.predict_proba(frame)[0]
    classes = list(package.model.classes_)
    probability = float(probabilities[classes.index(1)])
    if not math.isfinite(probability):
        raise RuntimeError("Model returned a non-finite probability.")
    predicted_class = int(probability >= threshold)
    return {
        "delay_probability": probability,
        "delay_risk": "Yes" if predicted_class else "No",
        "predicted_class": predicted_class,
    }
