import hashlib
import json
import logging
import math
import re
from numbers import Real

from flask import current_app

from siteintel.extensions import db
from siteintel.ml.model_registry import MODEL_SPECS, ModelArtifactError
from siteintel.ml.models import DocumentPrediction, PredictionHead
from siteintel.ml.predictors import (
    PredictionInputError,
    predict_cement_demand,
    predict_concrete_strength,
    predict_delay_risk,
)

logger = logging.getLogger(__name__)

MODELS = {
    "cement_demand": ("Cement demand", "tonnes", predict_cement_demand),
    "concrete_strength": ("Concrete strength", "MPa", predict_concrete_strength),
    "delay_risk": ("Construction delay risk", None, predict_delay_risk),
}

# Units and examples follow the existing prediction forms. Concrete feature
# names alone do not identify their units, so OCR values must include units.
FIELDS = {
    "planned_pour_tonnes": ("Planned pour", "tonnes", "40", None),
    "site_rolling_mean_30": ("30-day site demand average", "tonnes", "35", None),
    "rain_mm": ("Rainfall", "mm", "12", None),
    "avg_temp_c": ("Average temperature", "°C", "27", None),
    "cement": ("Cement", "kg/m³", "350", None),
    "slag": ("Slag", "kg/m³", "0", None),
    "water": ("Water", "kg/m³", "180", None),
    "age": ("Concrete age", "days", "28", None),
    "Material_Availability": ("Material availability", None, "Medium", ("Low", "Medium", "High")),
    "Equipment_Availability": ("Equipment availability", None, "High", ("Low", "Medium", "High")),
    "Weather_Risk": ("Weather risk", None, "Medium", ("Low", "Medium", "High")),
    "Supplier_Delay": ("Supplier delay", None, "Yes", ("No", "Yes")),
}
UNITS = {
    "planned_pour_tonnes": {"tonne", "tonnes", "t"},
    "site_rolling_mean_30": {"tonne", "tonnes", "t"},
    "rain_mm": {"mm"},
    "avg_temp_c": {"c", "°c"},
    "cement": {"kg/m3", "kg/m³", "kg/m^3"},
    "slag": {"kg/m3", "kg/m³", "kg/m^3"},
    "water": {"kg/m3", "kg/m³", "kg/m^3"},
    "age": {"day", "days"},
}
_NUMBER = re.compile(r"^([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*(.*?)$")
ALIASES = {
    "planned_pour_tonnes": ("planned pour", "planned pour tonnes", "planned pour volume", "planned pour quantity", "planned concrete pour", "planned concrete pour quantity"),
    "site_rolling_mean_30": ("30 day site demand average", "30 day site demand mean", "30 day rolling average", "30 day site rolling mean", "30 day average site demand", "30 day average demand", "30 day demand average"),
    "rain_mm": ("rain", "rainfall", "precipitation", "forecast rainfall", "expected rainfall", "daily rainfall"),
    "avg_temp_c": ("average temperature", "avg temperature", "mean temperature", "average temp", "avg temp", "mean temp", "daily average temperature"),
    "cement": ("cement", "cement content"),
    "slag": ("slag", "slag content", "blast furnace slag", "ggbs"),
    "water": ("water", "water content"),
    "age": ("age", "concrete age", "curing age", "test age", "age of concrete"),
    "Material_Availability": ("material availability",),
    "Equipment_Availability": ("equipment availability",),
    "Weather_Risk": ("weather risk",),
    "Supplier_Delay": ("supplier delay",),
}


def _label_key(label):
    return re.sub(r"[\s_.-]+", "", label).casefold()


def _label_parts(label):
    match = re.fullmatch(r"\s*(.*?)\s*\(([^()]*)\)\s*", label)
    return (match.group(1), match.group(2)) if match else (label, "")


def _feature(label):
    name, _ = _label_parts(label)
    key = _label_key(name)
    for feature, aliases in ALIASES.items():
        if key in {_label_key(feature), *(_label_key(alias) for alias in aliases)}:
            return feature
    return None


def _parse_value(feature, value, *, ocr=False):
    options = FIELDS[feature][3]
    if options:
        if not isinstance(value, str):
            raise PredictionInputError(f"{feature} must be one of: {', '.join(options)}.")
        matches = [option for option in options if option.casefold() == value.strip().casefold()]
        if not matches:
            raise PredictionInputError(f"{feature} must be one of: {', '.join(options)}.")
        return matches[0]
    if ocr:
        match = _NUMBER.fullmatch(value.strip()) if isinstance(value, str) else None
        if not match:
            raise PredictionInputError("Expected a number with the indicated unit.")
        unit = match.group(2).casefold().replace(" ", "")
        if (unit and unit not in UNITS[feature]) or (
            not unit and feature in {"cement", "slag", "water", "age"}
        ):
            raise PredictionInputError("The expected unit must be specified and match.")
        value = float(match.group(1))
    if isinstance(value, bool) or not isinstance(value, Real):
        raise PredictionInputError(f"{feature} must be a number.")
    try:
        value = float(value)
    except (ValueError, OverflowError) as error:
        raise PredictionInputError(f"{feature} must be finite.") from error
    if not math.isfinite(value) or (feature != "avg_temp_c" and value < 0):
        raise PredictionInputError(f"{feature} must be finite and within the allowed range.")
    return value


def fields_from_text(text):
    """Keep repeated labels so conflicting OCR values cannot be silently selected."""
    from siteintel.reports.extraction import extract_line_fields

    extracted = {}
    for line in text.splitlines():
        for label, value in extract_line_fields(line).items():
            extracted.setdefault(_label_key(label), []).append(value)
    return extracted


def ocr_inputs(model_key, text, corrections=None):
    from siteintel.reports.extraction import extract_line_fields

    corrections = corrections or {}
    labels = {}
    evidence = {}
    for line in text.splitlines():
        for label, value in extract_line_fields(line).items():
            feature = _feature(label)
            if feature is None:
                continue
            corrected = label in corrections
            value = corrections[label] if corrected else value
            _, unit = _label_parts(label)
            labels.setdefault(feature, []).append((value, unit, corrected))
            evidence.setdefault(feature, line.strip()[:300])
    inputs, missing = {}, {}
    for feature in MODEL_SPECS[model_key]["expected_features"]:
        values = [item for item in labels.get(feature, []) if item[0]]
        if not values:
            missing[feature] = "Not found in the report."
            continue
        try:
            parsed = []
            for value, label_unit, _ in values:
                match = _NUMBER.fullmatch(value.strip())
                if label_unit and match and not match.group(2):
                    value = f"{value} {label_unit}"
                elif label_unit and match and match.group(2):
                    if label_unit.casefold().replace(" ", "") != match.group(2).casefold().replace(" ", ""):
                        raise PredictionInputError("The label and value units conflict.")
                parsed.append(_parse_value(feature, value, ocr=True))
            if any(value != parsed[0] for value in parsed[1:]):
                raise PredictionInputError("The report contains conflicting values.")
        except PredictionInputError as error:
            missing[feature] = str(error)
        else:
            inputs[feature] = {
                "value": parsed[0],
                "source": "manual" if any(item[2] for item in values) else "ocr",
                "evidence": evidence[feature],
            }
    return inputs, missing


def create_prediction(document, user_id, model_key, inputs, missing):
    threshold = current_app.config["DELAY_RISK_THRESHOLD"] if model_key == "delay_risk" else None
    registry = current_app.extensions["siteintel_model_registry"]
    version_available = True
    try:
        version = registry.version(model_key)
    except (OSError, ModelArtifactError):
        logger.exception("Could not identify model version %s", model_key)
        version = "unavailable"
        version_available = False
    snapshot = {"inputs": inputs, "missing": missing, "threshold": threshold, "model_version": version}
    digest = hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    existing = db.session.scalar(
        db.select(DocumentPrediction).where(
            DocumentPrediction.document_id == document.id,
            DocumentPrediction.model_key == model_key,
            DocumentPrediction.input_hash == digest,
        )
    )
    if existing is not None and (existing.status != "unavailable" or not version_available):
        return existing
    status, result = "missing_information", None
    if not version_available:
        status = "unavailable"
    elif not missing:
        values = {name: item["value"] for name, item in inputs.items()}
        try:
            predictor = MODELS[model_key][2]
            kwargs = {"registry": registry}
            if threshold is not None:
                kwargs["threshold"] = threshold
            result = predictor(values, **kwargs)
            status = "complete"
        except (ModelArtifactError, PredictionInputError, OSError, ValueError, RuntimeError):
            logger.exception("Document prediction failed for model %s", model_key)
            status = "unavailable"
    if existing is not None:
        existing.status, existing.result = status, result
        return existing
    record = DocumentPrediction(
        document_id=document.id, created_by_id=user_id, model_key=model_key,
        input_hash=digest, status=status, inputs=inputs, missing=missing, result=result,
    )
    db.session.add(record)
    return record


def set_current(record):
    db.session.flush()
    head = db.session.get(PredictionHead, (record.document_id, record.model_key))
    if head is None:
        head = PredictionHead(document_id=record.document_id, model_key=record.model_key)
        db.session.add(head)
    head.prediction_id = record.id


def generate_for_document(document, user_id, text):
    records = []
    for model_key in MODELS:
        inputs, missing = ocr_inputs(model_key, text)
        records.append(create_prediction(document, user_id, model_key, inputs, missing))
    return records


def generate_for_review(document, user_id, text, original_fields, reviewed_fields, previous_fields):
    corrections = {label: value for label, value in reviewed_fields.items() if value != original_fields[label]}
    changed = {
        _feature(label) for label in original_fields
        if reviewed_fields[label] != previous_fields[label]
    }
    records = []
    for model_key in MODELS:
        inputs, missing = ocr_inputs(model_key, text, corrections)
        head = db.session.get(PredictionHead, (document.id, model_key))
        latest = head.prediction if head else None
        if latest:
            for feature, item in latest.inputs.items():
                if (
                    item["source"] == "manual"
                    and feature not in {_feature(label) for label in corrections}
                    and feature not in changed
                ):
                    inputs[feature] = item
                    missing.pop(feature, None)
        existing = create_prediction(document, user_id, model_key, inputs, missing)
        set_current(existing)
        records.append(existing)
    return records


def complete_prediction(record, user_id, values):
    if not isinstance(values, dict) or not values:
        raise PredictionInputError("Provide the missing information as an object.")
    unexpected = set(values) - set(record.missing)
    if unexpected:
        raise PredictionInputError("Only missing fields may be supplied.")
    inputs, missing = dict(record.inputs), dict(record.missing)
    for feature, value in values.items():
        inputs[feature] = {"value": _parse_value(feature, value), "source": "manual"}
        missing.pop(feature)
    return create_prediction(record.document, user_id, record.model_key, inputs, missing)


def serialize(record):
    name, unit, _ = MODELS[record.model_key]
    return {
        "id": record.id, "document_id": record.document_id,
        "document_name": record.document.file_name, "project_id": record.document.project_id,
        "model_key": record.model_key, "model_name": name, "unit": unit,
        "status": record.status, "result": record.result,
        "inputs": {
            feature: {**item, "label": FIELDS[feature][0], "unit": FIELDS[feature][1]}
            for feature, item in record.inputs.items()
        },
        "missing": [
            {"name": feature, "label": FIELDS[feature][0], "unit": FIELDS[feature][1],
             "example": FIELDS[feature][2], "options": FIELDS[feature][3], "reason": reason}
            for feature, reason in record.missing.items()
        ],
        "created_at": record.created_at.isoformat() if record.created_at else None,
    }
