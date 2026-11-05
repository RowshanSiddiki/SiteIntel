from collections.abc import Mapping
from typing import Any

from backend.modules.ml.model_loader import predict


def predict_supply_chain_stockout(features: Mapping[str, Any]) -> dict[str, Any]:
    return predict("supply_chain", features)
