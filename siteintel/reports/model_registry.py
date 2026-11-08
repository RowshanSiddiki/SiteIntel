from dataclasses import dataclass, replace
from functools import lru_cache
import logging
from pathlib import Path
from typing import Any

import joblib
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODEL_DIRECTORY = PROJECT_ROOT / "pkl_files"
WORKFORCE_FORBIDDEN = frozenset(
    {
        "risk_level",
        "risk_reason",
        "late_severity_band",
        "absence_category",
        "overtime_band",
        "manager_action_category",
        "manager_follow_up_required_flag",
        "coaching_recommended_flag",
        "employee_id",
        "employee_name",
        "record_id",
        "attendance_record_id",
        "replacement_employee_id",
    }
)

MODEL_SPECS = {
    "workforce": ("workforce_attendance_model", "risk_level"),
    "supply_chain": ("supply_chain_model", "Stockout_Flag"),
    "construction_estimate": ("construction_estimate_model", "Total_Estimate"),
    "civil_risk": ("civil_risk_model", "is_good"),
    "cement_demand": ("cement_demand_model", "consumed_tonnes"),
}
MODEL_FORBIDDEN = {
    "workforce": WORKFORCE_FORBIDDEN,
    "supply_chain": frozenset({"Stockout_Flag", "Demand_Forecast", "Date"}),
    "construction_estimate": frozenset({"Total_Estimate"}),
    "civil_risk": frozenset({"is_good", "Project ID"}),
    "cement_demand": frozenset({"consumed_tonnes", "date"}),
}
logger = logging.getLogger(__name__)


class ModelUnavailableError(RuntimeError):
    pass


def _pipeline_feature_types(model: Any) -> tuple[frozenset[str], frozenset[str]]:
    preprocessor = getattr(model, "named_steps", {}).get("preprocessor")
    if preprocessor is None:
        return frozenset(), frozenset()

    numeric: set[str] = set()
    categorical: set[str] = set()
    for name, _, columns in getattr(preprocessor, "transformers_", []):
        if not isinstance(columns, (list, tuple)):
            continue
        if name == "numeric":
            numeric.update(column for column in columns if isinstance(column, str))
        elif name == "categorical":
            categorical.update(
                column for column in columns if isinstance(column, str)
            )
    return frozenset(numeric), frozenset(categorical)


@dataclass(frozen=True)
class ModelArtifact:
    key: str
    target: str
    model: Any | None
    features: tuple[str, ...]
    numeric_features: frozenset[str]
    categorical_features: frozenset[str]
    issue: str | None = None

    @property
    def input_features(self) -> tuple[str, ...]:
        forbidden = MODEL_FORBIDDEN.get(self.key, frozenset())
        return tuple(feature for feature in self.features if feature not in forbidden)

    @property
    def usable(self) -> bool:
        return self.issue is None

    def _frame(self, values: dict[str, Any]) -> pd.DataFrame:
        if self.issue:
            raise ModelUnavailableError(self.issue)
        if tuple(values) != self.features:
            raise ValueError("Prediction input does not match model feature order.")
        return pd.DataFrame(
            [[values[name] for name in self.features]],
            columns=list(self.features),
        )

    def predict(self, values: dict[str, Any]) -> Any:
        return self.model.predict(self._frame(values))[0]

    def predict_probabilities(self, values: dict[str, Any]) -> dict[str, float] | None:
        predict_proba = getattr(self.model, "predict_proba", None)
        if not callable(predict_proba):
            return None
        probabilities = predict_proba(self._frame(values))[0]
        classes = getattr(self.model, "classes_", ())
        return {
            str(label): float(probability)
            for label, probability in zip(classes, probabilities)
        }


class ModelRegistry:
    def __init__(self, directory: Path = MODEL_DIRECTORY) -> None:
        self._models: dict[str, ModelArtifact] = {}
        for key, (stem, target) in MODEL_SPECS.items():
            try:
                self._models[key] = self._load(
                    key=key,
                    stem=stem,
                    expected_target=target,
                    directory=directory,
                )
            except Exception as error:
                logger.exception("Could not initialize model %s", key)
                self._models[key] = self._unavailable(
                    key=key,
                    stem=stem,
                    target=target,
                    directory=directory,
                    error=error,
                )

    @staticmethod
    def _unavailable(
        *,
        key: str,
        stem: str,
        target: str,
        directory: Path,
        error: Exception,
    ) -> ModelArtifact:
        features: tuple[str, ...] = ()
        numeric: frozenset[str] = frozenset()
        categorical: frozenset[str] = frozenset()
        details = f"{type(error).__name__}: {error}"
        try:
            metadata = joblib.load(directory / f"{stem}_metadata.pkl")
            if not isinstance(metadata, dict):
                raise ValueError("metadata is not a dictionary")
            declared_features = metadata.get("features")
            if declared_features is None:
                declared_features = metadata.get("feature_columns")
            if isinstance(declared_features, list) and all(
                isinstance(feature, str) for feature in declared_features
            ):
                features = tuple(declared_features)
                numeric = frozenset(metadata.get("numeric_features", []))
                categorical = frozenset(metadata.get("categorical_features", []))
                if not numeric and categorical:
                    numeric = frozenset(set(features) - set(categorical))
                elif numeric and not categorical:
                    categorical = frozenset(set(features) - set(numeric))
        except Exception as metadata_error:
            logger.exception("Could not read metadata for unavailable model %s", key)
            details += (
                f"; metadata load failed ({type(metadata_error).__name__}: "
                f"{metadata_error})"
            )
        return ModelArtifact(
            key=key,
            target=target,
            model=None,
            features=features,
            numeric_features=numeric,
            categorical_features=categorical,
            issue=f"Model unavailable: {details}",
        )

    @staticmethod
    def _load(
        *,
        key: str,
        stem: str,
        expected_target: str,
        directory: Path,
    ) -> ModelArtifact:
        model = joblib.load(directory / f"{stem}.pkl")
        metadata = joblib.load(directory / f"{stem}_metadata.pkl")
        if not isinstance(metadata, dict):
            raise RuntimeError(f"{stem} metadata must be a dictionary.")

        features = metadata.get("features")
        if features is None:
            features = metadata.get("feature_columns")
        if not isinstance(features, list) or not features or not all(
            isinstance(feature, str) for feature in features
        ):
            raise RuntimeError(f"{stem} metadata has no valid feature list.")
        if len(set(features)) != len(features):
            raise RuntimeError(f"{stem} metadata contains duplicate features.")

        target = metadata.get("target")
        if target != expected_target:
            raise RuntimeError(
                f"{stem} target is {target!r}; expected {expected_target!r}."
            )

        model_features = tuple(getattr(model, "feature_names_in_", ()))
        declared_features = tuple(features)
        forbidden = MODEL_FORBIDDEN[key].intersection(features)
        issue = None
        if model_features and model_features != declared_features:
            issue = "Model feature order differs from its metadata."
        elif target in features or forbidden:
            excluded = ", ".join(sorted(forbidden or {target}))
            issue = f"The model metadata includes excluded input feature(s): {excluded}."

        pipeline_numeric, pipeline_categorical = _pipeline_feature_types(model)
        numeric = frozenset(metadata.get("numeric_features", []))
        categorical = frozenset(metadata.get("categorical_features", []))
        if not numeric and not categorical:
            numeric, categorical = pipeline_numeric, pipeline_categorical
        elif not numeric:
            numeric = frozenset(set(features) - set(categorical))
        elif not categorical:
            categorical = frozenset(set(features) - set(numeric))
        if (
            numeric.intersection(categorical)
            or numeric.union(categorical) != set(features)
        ):
            issue = issue or "Feature data types are incomplete or inconsistent."

        artifact = ModelArtifact(
            key=key,
            target=target,
            model=model,
            features=declared_features,
            numeric_features=numeric,
            categorical_features=categorical,
            issue=issue,
        )
        if key == "workforce":
            forbidden_model_features = WORKFORCE_FORBIDDEN.intersection(
                model_features or features
            )
            if forbidden_model_features:
                forbidden_names = ", ".join(sorted(forbidden_model_features))
                artifact = replace(
                    artifact,
                    issue=(
                        "The workforce artifact requires excluded target, "
                        f"leakage, or identifier feature(s): {forbidden_names}."
                    ),
                )
        elif key == "supply_chain":
            classes = set(getattr(model, "classes_", ()))
            if not {0, 1}.issubset(classes):
                artifact = replace(
                    artifact,
                    issue=(
                        "The supply-chain artifact does not contain both "
                        "stockout classes 0 and 1."
                    ),
                )
        elif key == "civil_risk":
            classes = set(getattr(model, "classes_", ()))
            if not {0, 1}.issubset(classes):
                artifact = replace(
                    artifact,
                    issue="The civil-risk artifact does not contain classes 0 and 1.",
                )
        return artifact

    def get(self, key: str) -> ModelArtifact:
        try:
            return self._models[key]
        except KeyError as error:
            raise ValueError("Unknown prediction model.") from error

    def statuses(self) -> dict[str, dict[str, Any]]:
        return {
            key: {
                "available": artifact.usable,
                "issue": artifact.issue,
                "target": artifact.target,
                "feature_count": len(artifact.input_features),
            }
            for key, artifact in self._models.items()
        }


@lru_cache(maxsize=1)
def load_model_registry() -> ModelRegistry:
    return ModelRegistry()
