from dataclasses import dataclass, replace
from functools import lru_cache
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
    }
)


class ModelUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelArtifact:
    key: str
    target: str
    model: Any
    features: tuple[str, ...]
    numeric_features: frozenset[str]
    categorical_features: frozenset[str]
    issue: str | None = None

    @property
    def input_features(self) -> tuple[str, ...]:
        if self.key == "workforce":
            return tuple(
                feature
                for feature in self.features
                if feature not in WORKFORCE_FORBIDDEN
            )
        return self.features

    @property
    def usable(self) -> bool:
        return self.issue is None

    def predict(self, values: dict[str, Any]) -> Any:
        if self.issue:
            raise ModelUnavailableError(self.issue)
        if set(values) != set(self.features):
            raise ValueError("Prediction input does not match model metadata.")
        frame = pd.DataFrame(
            [[values[name] for name in self.features]],
            columns=list(self.features),
        )
        return self.model.predict(frame)[0]


class ModelRegistry:
    def __init__(self, directory: Path = MODEL_DIRECTORY) -> None:
        self._models = {
            "workforce": self._load(
                key="workforce",
                stem="workforce_attendance_model",
                expected_target="risk_level",
                directory=directory,
            ),
            "supply_chain": self._load(
                key="supply_chain",
                stem="supply_chain_model",
                expected_target="Stockout_Flag",
                directory=directory,
            ),
        }

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
        if not isinstance(features, list) or not features or not all(
            isinstance(feature, str) for feature in features
        ):
            raise RuntimeError(f"{stem} metadata has no valid features list.")
        target = metadata.get("target")
        if target != expected_target:
            raise RuntimeError(
                f"{stem} target is {target!r}; expected {expected_target!r}."
            )

        model_features = tuple(getattr(model, "feature_names_in_", ()))
        issue = None
        if model_features and model_features != tuple(features):
            issue = "Model feature order differs from its metadata."

        artifact = ModelArtifact(
            key=key,
            target=target,
            model=model,
            features=tuple(features),
            numeric_features=frozenset(metadata.get("numeric_features", [])),
            categorical_features=frozenset(
                metadata.get("categorical_features", [])
            ),
            issue=issue,
        )
        if key == "workforce":
            forbidden_model_features = WORKFORCE_FORBIDDEN.intersection(
                model_features or features
            )
            if forbidden_model_features:
                forbidden = ", ".join(sorted(forbidden_model_features))
                artifact = replace(
                    artifact,
                    issue=(
                        "The workforce artifact requires excluded target, "
                        f"leakage, or identifier feature(s): {forbidden}."
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
