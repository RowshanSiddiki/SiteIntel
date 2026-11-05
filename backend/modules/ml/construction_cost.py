from collections.abc import Mapping
from typing import Any

from backend.modules.ml.model_loader import predict


def predict_construction_cost(features: Mapping[str, Any]) -> dict[str, Any]:
    return predict("construction_cost", features)
