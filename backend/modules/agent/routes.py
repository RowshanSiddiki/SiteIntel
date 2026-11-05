from flask import Blueprint, current_app, g, jsonify, request

from backend.modules.agent.orchestrator import (
    AgentRequestError,
    handle_question,
)
from backend.modules.agent.service import AgentServiceError
from backend.modules.auth.routes import login_required


assistant_bp = Blueprint("assistant", __name__, url_prefix="/api/assistant")
MAX_REQUEST_BYTES = 65_536


@assistant_bp.post("/chat")
@login_required
def chat():
    if request.content_length is not None and request.content_length > MAX_REQUEST_BYTES:
        return jsonify(
            error={"code": 413, "message": "Request payload is too large."}
        ), 413
    if not request.is_json:
        return jsonify(error={"code": 400, "message": "A JSON request is required."}), 400
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify(error={"code": 400, "message": "A JSON object is required."}), 400
    unknown_fields = set(payload) - {"question", "inputs", "filters"}
    if unknown_fields:
        return jsonify(
            error={
                "code": 400,
                "message": "Unsupported request fields.",
                "fields": sorted(unknown_fields),
            }
        ), 400

    try:
        result = handle_question(
            question=payload.get("question"),
            inputs=payload.get("inputs"),
            filters=payload.get("filters"),
            user_id=g.current_user.id,
        )
    except AgentRequestError as error:
        return jsonify(error={"code": 400, "message": str(error)}), 400
    except AgentServiceError:
        current_app.logger.exception("Assistant LLM operation failed")
        return jsonify(
            error={"code": 503, "message": "The assistant service is unavailable."}
        ), 503

    status_codes = {
        "succeeded": 200,
        "explanation_unavailable": 200,
        "needs_input": 200,
        "no_matching_tool": 200,
        "invalid_input": 400,
        "integration_unavailable": 501,
        "tool_error": 503,
    }
    return jsonify(result), status_codes.get(result["status"], 500)
