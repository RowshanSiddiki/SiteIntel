from collections.abc import Mapping
from typing import Any

from backend.modules.ml.model_loader import predict


def predict_civil_risk(features: Mapping[str, Any]) -> dict[str, Any]:
    return predict("civil_risk", features)
