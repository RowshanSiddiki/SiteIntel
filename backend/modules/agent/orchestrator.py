from collections.abc import Mapping
from typing import Any

from backend.modules.agent.service import (
    AgentServiceError,
    explain_result,
    select_tool,
)
from backend.modules.agent.tools import (
    InvalidToolInput,
    ToolError,
    ToolUnavailable,
    execute_tool,
    expected_inputs,
)
from backend.modules.ml.model_loader import (
    InvalidPredictionInput,
    ModelError,
)


class AgentRequestError(ValueError):
    """Invalid question or request parameters."""


def _validate_request(
    question: Any, inputs: Any, filters: Any
) -> tuple[str, Mapping[str, Any], Mapping[str, Any]]:
    if not isinstance(question, str) or not question.strip():
        raise AgentRequestError("A non-empty question is required.")
    if len(question) > 4000:
        raise AgentRequestError("Question must be 4000 characters or fewer.")
    if inputs is not None and not isinstance(inputs, Mapping):
        raise AgentRequestError("inputs must be a JSON object.")
    if filters is not None and not isinstance(filters, Mapping):
        raise AgentRequestError("filters must be a JSON object.")
    return question.strip(), inputs or {}, filters or {}


def handle_question(
    *,
    question: Any,
    inputs: Any,
    filters: Any,
    user_id: int,
) -> dict[str, Any]:
    question, inputs, filters = _validate_request(question, inputs, filters)
    selection = select_tool(question)
    tool_name = selection["tool"]

    if tool_name == "none":
        return {
            "status": "no_matching_tool",
            "tool": None,
            "result": None,
            "response": "I could not identify an available SiteIntel tool for that question.",
        }

    if tool_name in {
        "workforce_prediction",
        "cement_demand_prediction",
        "supply_chain_prediction",
        "construction_cost_prediction",
        "civil_risk_prediction",
    } and not inputs:
        try:
            required = expected_inputs(tool_name)
        except ModelError:
            return {
                "status": "tool_error",
                "tool": tool_name,
                "result": None,
                "response": "The selected prediction model is unavailable.",
            }
        return {
            "status": "needs_input",
            "tool": tool_name,
            "required_inputs": list(required),
            "result": None,
            "response": (
                "Provide the model input fields to run this prediction. "
                "No values have been inferred."
            ),
        }

    try:
        result = execute_tool(
            tool_name,
            inputs=inputs,
            user_id=user_id,
            query=question,
            filters=filters,
        )
    except InvalidToolInput as error:
        return {
            "status": "invalid_input",
            "tool": tool_name,
            "result": None,
            "response": str(error),
        }
    except InvalidPredictionInput as error:
        return {
            "status": "invalid_input",
            "tool": tool_name,
            "result": None,
            "response": str(error),
        }
    except ToolUnavailable as error:
        return {
            "status": "integration_unavailable",
            "tool": tool_name,
            "result": None,
            "response": str(error),
        }
    except ToolError:
        return {
            "status": "tool_error",
            "tool": tool_name,
            "result": None,
            "response": "The selected SiteIntel tool could not complete the request.",
        }
    except ModelError:
        return {
            "status": "tool_error",
            "tool": tool_name,
            "result": None,
            "response": "The selected prediction model could not complete the request.",
        }

    try:
        response = explain_result(question, tool_name, result["data"])
    except AgentServiceError:
        return {
            "status": "explanation_unavailable",
            "tool": tool_name,
            "result": result,
            "response": "The tool completed; its structured result is available below.",
        }
    return {
        "status": "succeeded",
        "tool": tool_name,
        "result": result,
        "response": response,
    }
