from collections.abc import Mapping
from typing import Any

from backend.modules.ml.model_loader import predict


def predict_workforce_attendance(features: Mapping[str, Any]) -> dict[str, Any]:
    return predict("workforce_attendance", features)
