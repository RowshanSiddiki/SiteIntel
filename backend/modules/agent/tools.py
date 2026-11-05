from collections.abc import Callable, Mapping
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from typing import Any, Protocol

from flask import current_app

from backend.modules.ml.cement_demand import predict_cement_demand
from backend.modules.ml.civil_risk import predict_civil_risk
from backend.modules.ml.construction_cost import predict_construction_cost
from backend.modules.ml.model_loader import InvalidPredictionInput, load_model
from backend.modules.ml.supply_chain import predict_supply_chain_stockout
from backend.modules.ml.workforce import predict_workforce_attendance


class ToolError(RuntimeError):
    """Base error for approved SiteIntel tool failures."""


class ToolUnavailable(ToolError):
    """A future integration has not been configured."""


class InvalidToolInput(InvalidPredictionInput):
    """A tool input does not match the approved input schema."""


class RetrievalProvider(Protocol):
    """Provider contract; implementations must scope all reads to user_id."""

    def retrieve(
        self, *, user_id: int, query: str, filters: Mapping[str, Any]
    ) -> dict[str, Any]: ...


ML_TOOLS: dict[str, tuple[str, Callable[[Mapping[str, Any]], dict[str, Any]]]] = {
    "workforce_prediction": (
        "workforce_attendance",
        predict_workforce_attendance,
    ),
    "cement_demand_prediction": ("cement_demand", predict_cement_demand),
    "supply_chain_prediction": ("supply_chain", predict_supply_chain_stockout),
    "construction_cost_prediction": (
        "construction_cost",
        predict_construction_cost,
    ),
    "civil_risk_prediction": ("civil_risk", predict_civil_risk),
}


def expected_inputs(tool_name: str) -> tuple[str, ...]:
    try:
        model_key, _predictor = ML_TOOLS[tool_name]
    except KeyError as error:
        raise InvalidToolInput("The selected tool has no prediction schema.") from error
    return load_model(model_key).input_features


def _validate_exact_fields(
    inputs: Any, expected_fields: tuple[str, ...]
) -> Mapping[str, Any]:
    if not isinstance(inputs, Mapping):
        raise InvalidToolInput("Tool inputs must be a JSON object.")
    if any(not isinstance(key, str) for key in inputs):
        raise InvalidToolInput("Tool input names must be strings.")

    missing = [key for key in expected_fields if key not in inputs]
    unexpected = sorted(set(inputs) - set(expected_fields))
    if missing or unexpected:
        details = []
        if missing:
            details.append(f"missing features: {', '.join(missing)}")
        if unexpected:
            details.append(f"unexpected features: {', '.join(unexpected)}")
        raise InvalidToolInput("; ".join(details))
    return inputs


def _decimal_input(inputs: Mapping[str, Any], key: str) -> Decimal:
    value = inputs[key]
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise InvalidToolInput(f"'{key}' must be a finite number.")
    if isinstance(value, str) and len(value) > 64:
        raise InvalidToolInput(f"'{key}' is outside the supported numeric range.")
    try:
        result = Decimal(str(value))
    except InvalidOperation as error:
        raise InvalidToolInput(f"'{key}' must be a finite number.") from error
    if not result.is_finite():
        raise InvalidToolInput(f"'{key}' must be a finite number.")
    if (
        len(result.as_tuple().digits) > 32
        or (result != 0 and not -18 <= result.adjusted() <= 18)
    ):
        raise InvalidToolInput(f"'{key}' is outside the supported numeric range.")
    return result


def calculate_construction_estimate(inputs: Any) -> dict[str, Any]:
    required = (
        "Material_Cost",
        "Labor_Cost",
        "Profit_Rate",
        "Discount_or_Markup",
    )
    values = _validate_exact_fields(inputs, required)
    material = _decimal_input(values, "Material_Cost")
    labor = _decimal_input(values, "Labor_Cost")
    profit_rate = _decimal_input(values, "Profit_Rate")
    adjustment = _decimal_input(values, "Discount_or_Markup")
    if material < 0 or labor < 0 or profit_rate < 0:
        raise InvalidToolInput(
            "Material cost, labor cost, and profit rate cannot be negative."
        )

    subtotal = material + labor
    try:
        with localcontext() as context:
            context.prec = 64
            total = (
                subtotal + subtotal * profit_rate / Decimal("100") + adjustment
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation as error:
        raise InvalidToolInput("Inputs exceed the supported calculation range.") from error
    if total < 0:
        raise InvalidToolInput("The resulting construction estimate cannot be negative.")
    return {
        "tool": "construction_estimate_calculation",
        "total_estimate": format(total, "f"),
        "currency": None,
        "calculation": (
            "(Material_Cost + Labor_Cost) * (1 + Profit_Rate / 100) "
            "+ Discount_or_Markup"
        ),
    }


def _run_retrieval(
    tool_name: str,
    *,
    user_id: int,
    query: str,
    filters: Mapping[str, Any],
) -> dict[str, Any]:
    provider_key = (
        "documents" if tool_name == "document_retrieval" else "expenses"
    )
    providers = current_app.extensions.get("siteintel_retrieval_providers", {})
    provider = providers.get(provider_key)
    if provider is None:
        raise ToolUnavailable(
            f"The {provider_key} retrieval integration is not configured yet."
        )
    result = provider.retrieve(user_id=user_id, query=query, filters=filters)
    if not isinstance(result, dict):
        raise ToolError("Retrieval provider returned an invalid result.")
    return result


def execute_tool(
    tool_name: str,
    *,
    inputs: Any,
    user_id: int,
    query: str,
    filters: Any = None,
) -> dict[str, Any]:
    if tool_name in ML_TOOLS:
        expected = expected_inputs(tool_name)
        validated = _validate_exact_fields(inputs, expected)
        _model_key, predictor = ML_TOOLS[tool_name]
        result = predictor(validated)
        return {"tool": tool_name, "status": "succeeded", "data": result}

    if tool_name == "construction_estimate_calculation":
        result = calculate_construction_estimate(inputs)
        return {"tool": tool_name, "status": "succeeded", "data": result}

    if tool_name in {"document_retrieval", "expense_retrieval"}:
        if inputs not in (None, {}):
            raise InvalidToolInput(
                "Retrieval tools accept filters separately from prediction inputs."
            )
        if not isinstance(filters, Mapping):
            raise InvalidToolInput("Retrieval filters must be a JSON object.")
        result = _run_retrieval(
            tool_name,
            user_id=user_id,
            query=query,
            filters=filters,
        )
        return {"tool": tool_name, "status": "succeeded", "data": result}

    raise InvalidToolInput("The selected tool is not approved.")
