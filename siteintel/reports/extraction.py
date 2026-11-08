import math
import re
import unicodedata
from datetime import date, datetime, time, timedelta
from typing import Any

from siteintel.reports.model_registry import WORKFORCE_FORBIDDEN


FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "report_date": ("date", "report date", "work date", "attendance date"),
    "department": ("department", "division"),
    "team": ("team", "crew"),
    "manager_id": ("manager id",),
    "manager_name": ("manager name", "supervisor name"),
    "location": ("location", "site", "construction site"),
    "job_role": ("job role", "role", "position"),
    "employment_type": ("employment type", "worker type"),
    "hire_date": ("hire date", "start date"),
    "tenure_band": ("tenure", "tenure band", "length of service"),
    "day_of_week": ("day of week", "weekday"),
    "is_weekend": ("is weekend", "weekend"),
    "is_holiday": ("is holiday", "holiday"),
    "shift_name": ("shift", "shift name"),
    "scheduled_start_time": ("scheduled start", "scheduled start time"),
    "scheduled_end_time": ("scheduled end", "scheduled end time"),
    "scheduled_hours": ("scheduled hours", "hours scheduled"),
    "schedule_type": ("schedule type",),
    "attendance_status": ("attendance status", "status", "attendance"),
    "actual_clock_in": ("clock in", "actual clock in", "time in"),
    "actual_clock_out": ("clock out", "actual clock out", "time out"),
    "actual_hours_worked": ("actual hours worked", "hours worked", "worked hours"),
    "minutes_late": ("minutes late", "late minutes", "minutes delayed"),
    "minutes_left_early": ("minutes left early", "early departure minutes"),
    "absence_type": ("absence type", "absence reason"),
    "excused_flag": ("excused", "excused flag"),
    "no_call_no_show_flag": ("no call no show", "no call no show flag"),
    "partial_shift_flag": ("partial shift", "partial shift flag"),
    "late_flag": ("late flag", "late"),
    "absence_flag": ("absence flag", "absent"),
    "present_flag": ("present flag", "present"),
    "regular_hours": ("regular hours",),
    "overtime_hours": ("overtime hours", "overtime"),
    "hourly_rate": ("hourly rate", "pay rate"),
    "overtime_rate_multiplier": ("overtime rate multiplier", "overtime multiplier"),
    "regular_labor_cost": ("regular labor cost",),
    "overtime_labor_cost": ("overtime labor cost",),
    "total_labor_cost": ("total labor cost", "labor cost"),
    "overtime_flag": ("overtime flag",),
    "high_overtime_flag": ("high overtime flag",),
    "coverage_gap_flag": ("coverage gap", "coverage gap flag"),
    "replacement_needed_flag": ("replacement needed", "replacement needed flag"),
    "attendance_policy_exception_flag": (
        "attendance policy exception",
        "attendance policy exception flag",
    ),
    "data_quality_issue_flag": ("data quality issue", "data quality issue flag"),
    "data_quality_issue_type": ("data quality issue type",),
    "SKU_ID": ("sku id", "sku", "material code", "item code"),
    "Warehouse_ID": ("warehouse id", "warehouse", "storage location"),
    "Supplier_ID": ("supplier id", "supplier"),
    "Region": ("region",),
    "Units_Sold": ("units sold", "quantity used", "units consumed"),
    "Inventory_Level": (
        "inventory level",
        "current inventory",
        "stock on hand",
        "stock level",
    ),
    "Supplier_Lead_Time_Days": (
        "supplier lead time days",
        "supplier lead time",
        "lead time days",
    ),
    "Reorder_Point": ("reorder point", "reorder level", "minimum stock"),
    "Order_Quantity": ("order quantity", "quantity ordered"),
    "Unit_Cost": ("unit cost", "cost per unit"),
    "Unit_Price": ("unit price", "price per unit"),
    "Promotion_Flag": ("promotion flag", "promotion", "promo"),
    "Material_Cost": ("material cost", "materials cost", "materials total"),
    "Labor_Cost": ("labor cost", "labour cost", "labor total", "labour total"),
    "Profit_Rate": ("profit rate", "profit percentage", "margin rate"),
    "Discount_or_Markup": (
        "discount or markup",
        "discount",
        "markup",
        "discount markup",
    ),
    "Policy_Reason": ("policy reason", "pricing reason"),
    "Certificates": ("certificates", "certificate count"),
    "Years of Experience": ("years of experience", "experience years"),
    "Time Arrival Strafe": ("time arrival strafe",),
    "Project Cost": ("project cost", "project budget"),
    "Project Proximity": ("project proximity",),
    "Violation Risk Index": ("violation risk index",),
    "Company PCAB Score": ("company pcab score", "pcab score"),
    "Weekly Overtime Hours": ("weekly overtime hours", "weekly overtime"),
    "Salary Bracket": ("salary bracket",),
    "site_id": ("site id", "construction site id"),
    "cement_type": ("cement type",),
    "planned_pour_tonnes": ("planned pour tonnes", "planned pour"),
    "rain_mm": ("rain mm", "rainfall mm", "rainfall"),
    "avg_temp_c": ("avg temp c", "average temperature c", "temperature c"),
    "week_of_year": ("week of year", "week number"),
    "day_of_week": ("day of week", "weekday"),
    "quarter": ("quarter",),
    "site_demand_lag_1": ("site demand lag 1", "previous day site demand"),
    "site_demand_lag_7": ("site demand lag 7", "site demand 7 days ago"),
    "site_demand_lag_14": ("site demand lag 14", "site demand 14 days ago"),
    "site_rolling_mean_7": ("site rolling mean 7", "7 day site demand average"),
    "site_rolling_mean_30": ("site rolling mean 30", "30 day site demand average"),
}

WORKFORCE_REQUIRED = ("work_date", "attendance_status")
WORKFORCE_NUMERIC_EVIDENCE = (
    "minutes_late",
    "actual_hours_worked",
    "overtime_hours",
)
SUPPLY_REQUIRED = (
    "date_year",
    "date_month",
    "date_day",
    "date_dayofweek",
    "Units_Sold",
    "Inventory_Level",
    "Supplier_Lead_Time_Days",
    "Reorder_Point",
)

_NUMBER_PATTERN = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
_FLAG_FEATURES = frozenset(
    {
        "is_weekend",
        "is_holiday",
        "excused_flag",
        "no_call_no_show_flag",
        "partial_shift_flag",
        "late_flag",
        "absence_flag",
        "present_flag",
        "overtime_flag",
        "high_overtime_flag",
        "coverage_gap_flag",
        "replacement_needed_flag",
        "attendance_policy_exception_flag",
        "data_quality_issue_flag",
        "Promotion_Flag",
    }
)
_LABEL_PATTERN = re.compile(
    r"^\s*(?P<label>[A-Za-z][A-Za-z0-9 _./-]{0,60}?)\s*[:=]\s*(?P<value>.*?)\s*$"
)


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_value = "".join(
        char for char in decomposed if not unicodedata.combining(char)
    )
    return re.sub(r"[^a-z0-9]+", " ", ascii_value.lower()).strip()


def _parse_date(value: str) -> date | None:
    value = value.strip()
    formats = (
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%B %d, %Y",
        "%b %d, %Y",
        "%d %B %Y",
        "%d %b %Y",
    )
    for date_format in formats:
        try:
            return datetime.strptime(value, date_format).date()
        except ValueError:
            pass

    month_day_slash = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", value)
    if month_day_slash:
        month, day, year = (int(part) for part in month_day_slash.groups())
        try:
            return date(year, month, day)
        except ValueError:
            return None

    slash_or_dash = re.fullmatch(r"(\d{1,2})-(\d{1,2})-(\d{4})", value)
    if slash_or_dash:
        day, month, year = (int(part) for part in slash_or_dash.groups())
        try:
            return date(year, month, day)
        except ValueError:
            return None
    return None


def _parse_time(value: str) -> time | None:
    for format_string in ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M:%S %p"):
        try:
            return datetime.strptime(value.strip().upper(), format_string).time()
        except ValueError:
            pass
    return None


def _parse_numeric(value: str) -> int | float | None:
    match = _NUMBER_PATTERN.search(value.replace("\u2212", "-"))
    if not match:
        return None
    try:
        number = float(match.group().replace(",", ""))
    except ValueError:
        return None
    if not math.isfinite(number):
        return None
    return int(number) if number.is_integer() else number


def _parse_flag(value: str) -> int | None:
    normalized = _normalize(value)
    if normalized in {"yes", "true", "1", "y", "present", "late", "absent"}:
        return 1
    if normalized in {"no", "false", "0", "n", "not present", "on time"}:
        return 0
    return _parse_numeric(value)


def _feature_aliases(features: tuple[str, ...]) -> dict[str, str]:
    aliases: dict[str, str] = {
        "report date": "report_date",
        "date": "report_date",
        "work date": "report_date",
        "attendance date": "report_date",
    }
    for feature in features:
        if feature == "work_date":
            continue
        aliases[_normalize(feature)] = feature
        for alias in FIELD_ALIASES.get(feature, ()):
            aliases[_normalize(alias)] = feature
    aliases["report date"] = "report_date"
    aliases["attendance date"] = "report_date"
    return aliases


def _extract_labeled_values(text: str, features: tuple[str, ...]) -> dict[str, str]:
    aliases = _feature_aliases(features)
    extracted: dict[str, str] = {}
    for line in text.splitlines():
        match = _LABEL_PATTERN.match(line)
        if not match:
            continue
        field = aliases.get(_normalize(match.group("label")))
        value = match.group("value").strip()
        if field and value and field not in extracted:
            extracted[field] = value
    return extracted


def _extract_table_values(
    lines: list[dict[str, Any]], features: tuple[str, ...]
) -> tuple[dict[str, str], bool]:
    aliases = _feature_aliases(features)
    alias_tokens = {
        tuple(alias.split()): canonical
        for alias, canonical in aliases.items()
        if alias
    }
    found: dict[str, str] = {}
    ambiguous = False
    for index, header_line in enumerate(lines):
        tokens = header_line.get("tokens", [])
        words = [_normalize(str(token.get("text", ""))) for token in tokens]
        matches: list[tuple[int, float, str]] = []
        cursor = 0
        while cursor < len(words):
            matched = None
            for width in range(min(5, len(words) - cursor), 0, -1):
                canonical = alias_tokens.get(tuple(words[cursor : cursor + width]))
                if canonical:
                    matched = (width, canonical)
                    break
            if matched:
                width, canonical = matched
                center = sum(
                    float(token.get("center", 0))
                    for token in tokens[cursor : cursor + width]
                ) / width
                matches.append((cursor, center, canonical))
                cursor += width
            else:
                cursor += 1
        if len(matches) < 2:
            continue

        block = header_line.get("block")
        paragraph = header_line.get("paragraph")
        header_matches = sorted(matches, key=lambda match: match[1])
        boundaries = [
            (header_matches[position][1] + header_matches[position + 1][1]) / 2
            for position in range(len(header_matches) - 1)
        ]
        rows: list[list[list[str]]] = []
        for row in lines[index + 1 :]:
            if (
                row.get("block") != block
                or row.get("paragraph") != paragraph
            ):
                break
            cells: list[list[str]] = [[] for _ in header_matches]
            for token in row.get("tokens", []):
                center = float(token.get("center", 0))
                column = next(
                    (
                        position
                        for position, boundary in enumerate(boundaries)
                        if center < boundary
                    ),
                    len(header_matches) - 1,
                )
                cells[column].append(str(token.get("text", "")))
            if sum(bool(cell) for cell in cells) >= 2:
                rows.append(cells)

        if len(rows) > 1:
            ambiguous = True
            continue
        if rows:
            for match, cell_words in zip(header_matches, rows[0]):
                value = " ".join(cell_words).strip()
                if value:
                    found.setdefault(match[2], value)
    return ({} if ambiguous else found), ambiguous


def _typed_value(
    field: str,
    value: str,
    numeric_features: frozenset[str],
) -> Any:
    if field in numeric_features:
        if field in _FLAG_FEATURES:
            return _parse_flag(value)
        return _parse_numeric(value)
    return value.strip() or None


def _display_date(value: date) -> str:
    return f"{value.month}/{value.day}/{value.year}"


def extract_report_fields(
    text: str,
    *,
    workforce_features: tuple[str, ...],
    workforce_numeric: frozenset[str],
    supply_features: tuple[str, ...],
    supply_numeric: frozenset[str],
    ocr_lines: list[dict[str, Any]] | None = None,
    additional_models: dict[
        str, tuple[tuple[str, ...], frozenset[str]]
    ] | None = None,
) -> dict[str, Any]:
    workforce_raw = _extract_labeled_values(text, workforce_features)
    supply_raw = _extract_labeled_values(text, supply_features)
    additional_raw = {
        model_key: _extract_labeled_values(text, schema[0])
        for model_key, schema in (additional_models or {}).items()
    }
    ambiguities = {"workforce": False, "supply_chain": False}
    ambiguities.update({model_key: False for model_key in additional_raw})
    if ocr_lines:
        workforce_tables, ambiguities["workforce"] = _extract_table_values(
            ocr_lines, workforce_features
        )
        supply_tables, ambiguities["supply_chain"] = _extract_table_values(
            ocr_lines, supply_features
        )
        for field, value in workforce_tables.items():
            workforce_raw.setdefault(field, value)
        for field, value in supply_tables.items():
            supply_raw.setdefault(field, value)
        for model_key, schema in (additional_models or {}).items():
            table_values, ambiguities[model_key] = _extract_table_values(
                ocr_lines, schema[0]
            )
            for field, value in table_values.items():
                additional_raw[model_key].setdefault(field, value)

    report_date_raw = (
        workforce_raw.get("report_date")
        or supply_raw.get("report_date")
        or workforce_raw.get("work_date")
        or supply_raw.get("work_date")
        or next(
            (
                raw_values.get("report_date")
                or raw_values.get("date")
                or raw_values.get("work_date")
                for raw_values in additional_raw.values()
                if raw_values.get("report_date")
                or raw_values.get("date")
                or raw_values.get("work_date")
            ),
            None,
        )
    )
    parsed_date = _parse_date(report_date_raw) if report_date_raw else None

    workforce: dict[str, Any] = {
        field: None for field in workforce_features if field not in WORKFORCE_FORBIDDEN
    }
    supply: dict[str, Any] = {field: None for field in supply_features}
    additional: dict[str, dict[str, Any]] = {
        model_key: {field: None for field in schema[0]}
        for model_key, schema in (additional_models or {}).items()
    }

    for field, raw_value in workforce_raw.items():
        if field in workforce and field != "report_date":
            workforce[field] = _typed_value(field, raw_value, workforce_numeric)
    for field, raw_value in supply_raw.items():
        if field in supply and field != "report_date":
            supply[field] = _typed_value(field, raw_value, supply_numeric)
    for model_key, values in additional_raw.items():
        numeric_features = (additional_models or {})[model_key][1]
        for field, raw_value in values.items():
            if field in additional[model_key] and field not in {
                "report_date",
                "date",
            }:
                additional[model_key][field] = _typed_value(
                    field, raw_value, numeric_features
                )

    if parsed_date:
        iso_date = _display_date(parsed_date)
        for field in ("work_date",):
            if field in workforce:
                workforce[field] = iso_date
        if "day_of_week" in workforce and workforce["day_of_week"] is None:
            workforce["day_of_week"] = parsed_date.strftime("%A")
        if "week_start_date" in workforce and workforce["week_start_date"] is None:
            week_start = parsed_date - timedelta(days=parsed_date.weekday())
            workforce["week_start_date"] = _display_date(week_start)
        if "month_start_date" in workforce and workforce["month_start_date"] is None:
            workforce["month_start_date"] = _display_date(
                parsed_date.replace(day=1)
            )
        if "is_weekend" in workforce and workforce["is_weekend"] is None:
            workforce["is_weekend"] = int(parsed_date.weekday() >= 5)
        supply.update(
            {
                "date_year": parsed_date.year,
                "date_month": parsed_date.month,
                "date_day": parsed_date.day,
                "date_dayofweek": parsed_date.weekday(),
            }
        )
        for model_values in additional.values():
            if "date_year" in model_values:
                model_values["date_year"] = parsed_date.year
            if "date_month" in model_values:
                model_values["date_month"] = parsed_date.month
            if "date_day" in model_values:
                model_values["date_day"] = parsed_date.day
            if "date_dayofweek" in model_values:
                model_values["date_dayofweek"] = parsed_date.weekday()

    scheduled_start = _parse_time(str(workforce.get("scheduled_start_time") or ""))
    scheduled_end = _parse_time(str(workforce.get("scheduled_end_time") or ""))
    if scheduled_start and scheduled_end:
        scheduled_start_minutes = scheduled_start.hour * 60 + scheduled_start.minute
        scheduled_end_minutes = scheduled_end.hour * 60 + scheduled_end.minute
        if scheduled_end_minutes < scheduled_start_minutes:
            scheduled_end_minutes += 24 * 60
        if workforce.get("scheduled_hours") is None:
            workforce["scheduled_hours"] = (
                scheduled_end_minutes - scheduled_start_minutes
            ) / 60

    if workforce.get("attendance_status"):
        status = _normalize(str(workforce["attendance_status"]))
        if status in {"present", "on site", "at work"}:
            workforce["present_flag"] = 1
            workforce["absence_flag"] = 0
        elif status in {"absent", "no show", "no call no show"}:
            workforce["present_flag"] = 0
            workforce["absence_flag"] = 1

    actual_start = _parse_time(str(workforce.get("actual_clock_in") or ""))
    actual_end = _parse_time(str(workforce.get("actual_clock_out") or ""))
    if actual_start and scheduled_start and workforce.get("minutes_late") is None:
        actual_minutes = actual_start.hour * 60 + actual_start.minute
        scheduled_minutes = scheduled_start.hour * 60 + scheduled_start.minute
        workforce["minutes_late"] = max(0, actual_minutes - scheduled_minutes)
    if actual_end and scheduled_end and workforce.get("minutes_left_early") is None:
        actual_minutes = actual_end.hour * 60 + actual_end.minute
        scheduled_minutes = scheduled_end.hour * 60 + scheduled_end.minute
        if actual_minutes < scheduled_minutes and scheduled_start:
            if scheduled_start.hour > scheduled_end.hour:
                actual_minutes += 24 * 60
                scheduled_minutes += 24 * 60
        workforce["minutes_left_early"] = max(0, scheduled_minutes - actual_minutes)
    if (
        actual_start
        and actual_end
        and workforce.get("actual_hours_worked") is None
    ):
        start_minutes = actual_start.hour * 60 + actual_start.minute
        end_minutes = actual_end.hour * 60 + actual_end.minute
        if end_minutes < start_minutes:
            end_minutes += 24 * 60
        workforce["actual_hours_worked"] = (end_minutes - start_minutes) / 60

    if workforce.get("minutes_late") is not None:
        workforce["late_flag"] = int(workforce["minutes_late"] > 0)
    if workforce.get("overtime_hours") is not None:
        workforce["overtime_flag"] = int(workforce["overtime_hours"] > 0)

    return {
        "report_date": report_date_raw,
        "workforce": workforce,
        "supply_chain": supply,
        **additional,
        "ambiguities": ambiguities,
    }


def calculate_readiness(
    fields: dict[str, Any],
    *,
    workforce_available: bool,
    workforce_issue: str | None,
    supply_available: bool,
    supply_issue: str | None,
    additional_model_status: dict[str, tuple[bool, str | None]] | None = None,
) -> dict[str, dict[str, Any]]:
    workforce = fields["workforce"]
    missing_workforce = [
        field
        for field in WORKFORCE_REQUIRED
        if workforce.get(field) is None
    ]
    workforce_ambiguity = fields.get("ambiguities", {}).get("workforce", False)
    if not any(workforce.get(field) is not None for field in WORKFORCE_NUMERIC_EVIDENCE):
        missing_workforce.append(
            "one of: " + ", ".join(WORKFORCE_NUMERIC_EVIDENCE)
        )

    supply = fields["supply_chain"]
    missing_supply = [field for field in SUPPLY_REQUIRED if supply.get(field) is None]
    supply_ambiguity = fields.get("ambiguities", {}).get("supply_chain", False)
    workforce_data_issue = (
        "Multiple workforce records were detected; the report must identify one "
        "record before a single attendance prediction can be prepared."
        if workforce_ambiguity
        else None
    )
    supply_data_issue = (
        "Multiple supply-chain records were detected; the report must identify one "
        "record before a single stockout prediction can be prepared."
        if supply_ambiguity
        else None
    )

    readiness = {
        "workforce": {
            "data_ready": not missing_workforce and workforce_data_issue is None,
            "missing_fields": missing_workforce,
            "required_features": list(workforce),
            "available_inputs": {
                field: value
                for field, value in workforce.items()
                if value is not None and value != ""
            },
            "data_issue": workforce_data_issue,
            "model_available": workforce_available,
            "model_status": "available" if workforce_available else "unavailable",
            "model_issue": workforce_issue,
            "prediction_enabled": (
                not missing_workforce
                and workforce_data_issue is None
                and workforce_available
            ),
        },
        "supply_chain": {
            "data_ready": not missing_supply and supply_data_issue is None,
            "missing_fields": missing_supply,
            "required_features": list(supply),
            "available_inputs": {
                field: value
                for field, value in supply.items()
                if value is not None and value != ""
            },
            "data_issue": supply_data_issue,
            "model_available": supply_available,
            "model_status": "available" if supply_available else "unavailable",
            "model_issue": supply_issue,
            "prediction_enabled": (
                not missing_supply and supply_data_issue is None and supply_available
            ),
        },
    }
    warnings = {
        "construction_estimate": (
            "This model uses material cost, labor cost, profit rate, discount or "
            "markup, and policy reason to estimate Total_Estimate. Because its "
            "component costs directly determine the target in the training data, "
            "this is not an independent cost estimate. The dataset does not "
            "document a currency or unit."
        ),
        "civil_risk": (
            "This is a model-based project assessment, not a definitive judgment. "
            "The model includes age and experience; do not use it for automated "
            "employment or other consequential decisions."
        ),
        "cement_demand": (
            "This is the model's estimated consumption for the supplied date and "
            "inputs, not a guaranteed future demand value. In the checked-in "
            "training dataset, consumed_tonnes exactly equals planned_pour_tonnes "
            "in 19,195 of 31,980 rows, which may limit model reliability."
        ),
    }
    for model_key, (model_available, model_issue) in (
        additional_model_status or {}
    ).items():
        model_values = fields[model_key]
        missing_fields = [
            feature for feature, value in model_values.items() if value is None
        ]
        ambiguous = fields.get("ambiguities", {}).get(model_key, False)
        if not model_values:
            data_issue = (
                "Model input feature metadata is unavailable; readiness cannot "
                "be evaluated."
            )
        elif ambiguous:
            data_issue = (
                "Multiple records for this model were detected; the report must "
                "identify one record before a single prediction can be prepared."
            )
        else:
            data_issue = None
        data_ready = bool(model_values) and not missing_fields and data_issue is None
        readiness[model_key] = {
            "data_ready": data_ready,
            "missing_fields": missing_fields,
            "required_features": list(model_values),
            "available_inputs": {
                field: value
                for field, value in model_values.items()
                if value is not None and value != ""
            },
            "data_issue": data_issue,
            "model_available": model_available,
            "model_status": "available" if model_available else "unavailable",
            "model_issue": model_issue,
            "model_warning": warnings.get(model_key),
            "prediction_enabled": data_ready and model_available,
        }
    return readiness


def feature_groups(fields: dict[str, Any]) -> dict[str, dict[str, Any]]:
    workforce = {
        key: value
        for key, value in fields["workforce"].items()
        if value is not None
    }
    supply = {
        key: value
        for key, value in fields["supply_chain"].items()
        if value is not None
    }
    groups = {
        "report": {"report_date": fields.get("report_date")},
        "workforce": workforce,
        "supply_chain": supply,
    }
    for model_key in (
        "construction_estimate",
        "civil_risk",
        "cement_demand",
    ):
        values = fields.get(model_key)
        if values is not None:
            groups[model_key] = {
                key: value for key, value in values.items() if value is not None
            }
    return groups
