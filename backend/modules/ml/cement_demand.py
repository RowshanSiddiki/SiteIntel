from collections.abc import Mapping
from typing import Any

from backend.modules.ml.model_loader import predict


def predict_cement_demand(features: Mapping[str, Any]) -> dict[str, Any]:
    return predict("cement_demand", features)
