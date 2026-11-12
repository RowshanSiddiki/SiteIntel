import logging
from functools import wraps
from typing import Any, Callable, TypeVar

from flask import Blueprint, current_app, jsonify, request
from flask_login import current_user

from siteintel.ml.model_registry import ModelArtifactError
from siteintel.ml.predictors import (
    PredictionInputError,
    predict_cement_demand,
    predict_concrete_strength,
    predict_delay_risk,
)


ml_bp = Blueprint("ml", __name__, url_prefix="/api")
logger = logging.getLogger(__name__)
F = TypeVar("F", bound=Callable[..., Any])


def _error(code: str, message: str, status: int):
    return jsonify({"success": False, "error": {"code": code, "message": message}}), status


def require_authenticated_api(view: F) -> F:
    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any):
        if not current_user.is_authenticated:
            return _error("authentication_required", "Please log in to continue.", 401)
        return view(*args, **kwargs)

    return wrapped  # type: ignore[return-value]


def _request_values():
    if not request.is_json:
        return None, _error(
            "json_required",
            "Send prediction inputs as a JSON object.",
            415,
        )
    values = request.get_json(silent=True)
    if not isinstance(values, dict):
        return None, _error(
            "invalid_json",
            "Request body must be a valid JSON object.",
            400,
        )
    return values, None


def _predict(predictor: Callable[..., dict[str, Any]], *, threshold: float | None = None):
    values, error = _request_values()
    if error is not None:
        return error

    registry = current_app.extensions["siteintel_model_registry"]
    try:
        if threshold is None:
            prediction = predictor(values, registry=registry)
        else:
            prediction = predictor(
                values,
                threshold=threshold,
                registry=registry,
            )
    except PredictionInputError as error:
        return _error("invalid_input", str(error), 400)
    except (ModelArtifactError, OSError):
        logger.exception("Prediction model artifacts are unavailable")
        return _error(
            "model_unavailable",
            "The prediction model is unavailable. Please try again later.",
            503,
        )
    except Exception:
        logger.exception("Prediction failed")
        return _error(
            "prediction_failed",
            "The model could not produce a prediction for these inputs.",
            500,
        )
    return jsonify({"success": True, "prediction": prediction})


@ml_bp.post("/ml/cement-demand")
@require_authenticated_api
def cement_demand():
    return _predict(predict_cement_demand)


@ml_bp.post("/ml/concrete-strength")
@require_authenticated_api
def concrete_strength():
    return _predict(predict_concrete_strength)


@ml_bp.post("/ml/delay-risk")
@require_authenticated_api
def delay_risk():
    threshold = current_app.config["DELAY_RISK_THRESHOLD"]
    return _predict(predict_delay_risk, threshold=threshold)
