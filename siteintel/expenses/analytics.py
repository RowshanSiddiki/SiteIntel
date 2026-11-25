"""Chart data built only from confirmed daily reports and verified finance records.

Every parser is deliberately strict: a value that is not clearly a number
(with a unit where one is required) is skipped, never guessed.
"""
import re
from collections import defaultdict

from sqlalchemy import func

from siteintel.extensions import db
from siteintel.projects.models import Document, FinancialObservation
from siteintel.reports.models import DailyConstructionReport

NUMBER = r"\d[\d,]*(?:\.\d+)?"
QUANTITY = re.compile(
    rf"^\s*({NUMBER})\s*(kg|kgs|bags?|m3|m³|cum|cft|sqft|sft|pcs|nos|tons?|tonnes?|ltr|litres?|liters?|bundles?)\s*\.?\s*$",
    re.I,
)
COUNT = re.compile(r"^\s*(\d{1,4})\s*\.?\s*$")
TEMPERATURE = re.compile(r"(-?\d{1,2}(?:\.\d+)?)\s*°\s*C\b|(-?\d{1,2}(?:\.\d+)?)\s*(?:deg(?:rees?)?\s*)?C\b", re.I)
RAINFALL = re.compile(r"rain\w*[^\n]*:\s*\n?\s*[^\d\n]*?(\d+(?:\.\d+)?)\s*mm|rain\w*\s*[=-]?\s*(\d+(?:\.\d+)?)\s*mm", re.I)
PERCENT = re.compile(r"^\s*(\d{1,3}(?:\.\d+)?)\s*%\s*$")
TRADES = {
    "mason": "Mason", "masons": "Mason", "helper": "Helper", "helpers": "Helper",
    "rod binder": "Rod binder", "rod binders": "Rod binder", "electrician": "Electrician",
    "electricians": "Electrician", "carpenter": "Carpenter", "carpenters": "Carpenter",
    "plumber": "Plumber", "plumbers": "Plumber", "painter": "Painter", "painters": "Painter",
    "welder": "Welder", "welders": "Welder", "labourer": "Labourer", "labourers": "Labourer",
    "laborer": "Labourer", "laborers": "Labourer", "steel fixer": "Steel fixer",
    "steel fixers": "Steel fixer", "shuttering": "Shuttering carpenter",
}
EQUIPMENT_WORDS = ("pump", "machine", "mixer", "vibrator", "excavator", "crane", "roller",
                   "loader", "generator", "compressor", "cutter", "grinder", "drill", "scaffold")
OPERATIONAL = ("operational", "working", "available", "running", "in use", "good condition")
ATTENTION = ("maintenance", "repair", "broken", "breakdown", "faulty", "out of service", "not working")
COMPLETION_HEADINGS = {"completed", "work done today", "work completed", "done today", "completed today"}
HEADING = re.compile(r"^[A-Za-z][A-Za-z ./&-]{1,40}:\s*$")
BULLET = re.compile(r"^\s*(?:[-•*]|\d+[.)])\s+(.+)$")


def _number(text):
    return float(text.replace(",", ""))


def _items(report):
    for section in (report.fields or {}).get("sections", []):
        for item in section.get("items", []):
            label, value = str(item.get("label", "")).strip(), str(item.get("value", "")).strip()
            if label and value:
                yield section.get("name", ""), label, value, int(item.get("source_document_id") or 0)


def _latest(values):
    """Keep one value per key; the highest source document wins, deterministically."""
    chosen = {}
    for key, source, value in values:
        if key not in chosen or source >= chosen[key][0]:
            chosen[key] = (source, value)
    return {key: value for key, (_, value) in chosen.items()}


def _materials(report):
    rows, current = [], {}
    for section, label, value, source in _items(report):
        if section != "Materials and quantities":
            continue
        name = None
        if label.lower() == "material":
            current[source] = value
            continue
        if label.lower() == "quantity":
            name = current.get(source)
        elif not label.lower().startswith(("note", "remark", "supplier", "vendor")):
            name = label
        match = QUANTITY.match(value)
        if name and match and len(name) <= 60:
            rows.append(((name.strip().title(), match.group(2).lower().rstrip("s")), source, _number(match.group(1))))
    return _latest(rows)


def _labour(report):
    rows = []
    for _section, label, value, source in _items(report):
        trade = TRADES.get(label.lower().rstrip(":"))
        match = COUNT.match(value)
        if trade and match:
            rows.append((trade, source, int(match.group(1))))
    return _latest(rows)


def _equipment(report):
    rows = []
    for _section, label, value, source in _items(report):
        low = value.lower()
        if len(label) > 50 or not any(word in label.lower() for word in EQUIPMENT_WORDS):
            continue
        if any(word in low for word in ATTENTION):
            rows.append((label.title(), source, "attention"))
        elif any(word in low for word in OPERATIONAL):
            rows.append((label.title(), source, "operational"))
    return _latest(rows)


def _weather(report):
    temperature = rainfall = None
    for _section, label, value, _source in _items(report):
        if temperature is None and "temperature" in label.lower() and len(value) <= 20:
            match = TEMPERATURE.search(value)
            if match:
                temperature = float(match.group(1) or match.group(2))
        if rainfall is None:
            match = RAINFALL.search(value if "rain" in value.lower() else f"{label}: {value}")
            if match and "rain" in f"{label} {value}".lower():
                rainfall = float(match.group(1) or match.group(2))
    return temperature, rainfall


def _progress(report):
    activities, percent = {}, None
    for _section, label, value, _source in _items(report):
        if "progress" in label.lower():
            match = PERCENT.match(value)
            if match and float(match.group(1)) <= 100:
                percent = float(match.group(1))
        collecting = False
        for line in value.splitlines():
            stripped = line.strip()
            if HEADING.match(stripped):
                collecting = stripped.rstrip(":").strip().lower() in COMPLETION_HEADINGS
                continue
            bullet = BULLET.match(stripped)
            if collecting and bullet:
                text = re.sub(r"\s+", " ", bullet.group(1)).strip().lower().rstrip(".")
                activities[text] = True
    return len(activities), percent


def _expense_series(project_id, conditions):
    rows = db.session.execute(
        db.select(FinancialObservation.currency, FinancialObservation.observed_date,
                  func.sum(FinancialObservation.amount))
        .join(Document).where(*conditions, FinancialObservation.kind.in_(("invoice_total", "direct_expense")),
                              FinancialObservation.observed_date.is_not(None))
        .group_by(FinancialObservation.currency, FinancialObservation.observed_date)
        .order_by(FinancialObservation.observed_date)
    ).all()
    by_currency = defaultdict(list)
    for currency, day, amount in rows:
        by_currency[currency].append((day, float(amount)))
    if not by_currency:
        return {"dates": [], "series": [], "unit": ""}
    currency = max(by_currency, key=lambda key: (len(by_currency[key]), key))
    points = by_currency[currency]
    return {"dates": [day.isoformat() for day, _ in points], "unit": currency,
            "series": [{"name": "Recorded expense", "values": [amount for _, amount in points]}],
            "other_currencies": sorted(set(by_currency) - {currency})}


def _align(dates, per_date, names):
    return [{"name": name, "values": [per_date[day].get(name) for day in dates]} for name in names]


def build(project_id, dates, expense_conditions):
    query = db.select(DailyConstructionReport).where(DailyConstructionReport.project_id == project_id)
    if "start" in dates:
        query = query.where(DailyConstructionReport.reporting_date >= dates["start"])
    if "end" in dates:
        query = query.where(DailyConstructionReport.reporting_date <= dates["end"])
    reports = db.session.scalars(query.order_by(DailyConstructionReport.reporting_date)).all()

    material, labour, equipment, progress = {}, {}, {}, {}
    temperature, rainfall, percent = {}, {}, {}
    for report in reports:
        day = report.reporting_date.isoformat()
        material[day], labour[day], equipment[day] = _materials(report), _labour(report), _equipment(report)
        count, pct = _progress(report)
        if count:
            progress[day] = count
        if pct is not None:
            percent[day] = pct
        temp, rain = _weather(report)
        if temp is not None:
            temperature[day] = temp
        if rain is not None:
            rainfall[day] = rain

    def used(per_date):
        return [day for day in per_date if per_date[day]]

    material_days = used(material)
    totals = defaultdict(float)
    for day in material_days:
        for key, amount in material[day].items():
            totals[key] += amount
    unit_names = defaultdict(list)
    for name, unit in totals:
        unit_names[unit].append(name)
    if unit_names:
        unit = max(unit_names, key=lambda key: (len(unit_names[key]), sum(totals[(n, key)] for n in unit_names[key])))
        top = sorted(unit_names[unit], key=lambda name: -totals[(name, unit)])[:6]
        material_chart = {"dates": material_days, "unit": unit, "series": [
            {"name": name, "values": [material[day].get((name, unit)) for day in material_days]} for name in top]}
    else:
        material_chart = {"dates": [], "unit": "", "series": []}

    labour_days = used(labour)
    trades = sorted({trade for day in labour_days for trade in labour[day]})
    equipment_days = used(equipment)
    equipment_series = []
    if equipment_days:
        equipment_series = [
            {"name": "Operational", "values": [sum(1 for s in equipment[d].values() if s == "operational") for d in equipment_days]},
            {"name": "Needs attention", "values": [sum(1 for s in equipment[d].values() if s == "attention") for d in equipment_days]},
        ]
    progress_days = sorted(set(progress) | set(percent))
    progress_series = []
    if progress:
        progress_series.append({"name": "Activities completed", "values": [progress.get(d) for d in progress_days]})
    if percent:
        progress_series.append({"name": "Progress %", "values": [percent.get(d) for d in progress_days], "axis": "right"})
    weather_days = sorted(set(temperature) | set(rainfall))
    weather_series = []
    if temperature:
        weather_series.append({"name": "Temperature (°C)", "values": [temperature.get(d) for d in weather_days]})
    if rainfall:
        weather_series.append({"name": "Rainfall (mm)", "values": [rainfall.get(d) for d in weather_days], "axis": "right"})

    expense = _expense_series(project_id, expense_conditions)
    latest_labour = labour[labour_days[-1]] if labour_days else {}
    return {
        "range": {"start": reports[0].reporting_date.isoformat() if reports else None,
                  "end": reports[-1].reporting_date.isoformat() if reports else None},
        "kpis": {
            "reports": len(reports),
            "expenses": sum(expense["series"][0]["values"]) if expense["series"] else None,
            "expense_unit": expense["unit"],
            "workforce": sum(latest_labour.values()) if latest_labour else None,
            "activities": sum(progress.values()) if progress else None,
        },
        "charts": {
            "expense": expense,
            "material": material_chart,
            "labour": {"dates": labour_days, "unit": "workers", "series": _align(labour_days, labour, trades)},
            "progress": {"dates": progress_days, "unit": "", "series": progress_series},
            "equipment": {"dates": equipment_days, "unit": "items", "series": equipment_series},
            "weather": {"dates": weather_days, "unit": "", "series": weather_series},
        },
    }
