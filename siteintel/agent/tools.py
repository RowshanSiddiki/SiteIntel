"""Validated, read-only tools for the project AI Agent."""
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import case, func

from siteintel.extensions import db
from siteintel.ml.document_predictions import serialize as serialize_prediction
from siteintel.ml.models import DocumentPrediction, PredictionHead
from siteintel.projects.models import (
    Document,
    Expense,
    ExpenseBill,
    FinancialObservation,
    Project,
)
from siteintel.reports.models import DailyConstructionReport


MODEL_NAMES = {
    "cement_demand": "Cement demand",
    "concrete_strength": "Concrete strength",
    "delay_risk": "Construction delay risk",
}

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "get_project_overview",
            "description": "Retrieve the current project's basic details and record counts.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_daily_reports",
            "description": "Retrieve generated daily construction reports for a date range.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {"type": "string", "description": "Inclusive YYYY-MM-DD date."},
                    "end_date": {"type": "string", "description": "Inclusive YYYY-MM-DD date."},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 10},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_project_documents",
            "description": "Search indexed project documents using the existing project RAG index.",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "minLength": 3, "maxLength": 1000},
                },
                "required": ["question"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_ml_predictions",
            "description": "Retrieve current saved prediction results; does not run or retrain models.",
            "parameters": {
                "type": "object",
                "properties": {
                    "model_key": {
                        "type": "string",
                        "enum": list(MODEL_NAMES),
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_expense_summary",
            "description": "Summarize project expenses, payment status, and confirmed document-derived figures.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {"type": "string", "description": "Inclusive YYYY-MM-DD date."},
                    "end_date": {"type": "string", "description": "Inclusive YYYY-MM-DD date."},
                    "category": {"type": "string", "maxLength": 64},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compare_project_progress",
            "description": "Compare fields from two generated daily reports by reporting date.",
            "parameters": {
                "type": "object",
                "properties": {
                    "from_date": {"type": "string", "description": "First YYYY-MM-DD reporting date."},
                    "to_date": {"type": "string", "description": "Second YYYY-MM-DD reporting date."},
                },
                "required": ["from_date", "to_date"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_project_summary",
            "description": "Gather current project, recent report, saved prediction, and expense facts for a grounded summary.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
]

TOOL_LABELS = {
    "get_project_overview": "Project overview",
    "get_daily_reports": "Daily construction reports",
    "search_project_documents": "Project document search",
    "get_ml_predictions": "Saved ML predictions",
    "get_expense_summary": "Expenses and bills",
    "compare_project_progress": "Progress comparison",
    "generate_project_summary": "Project summary facts",
}

_ARGUMENT_KEYS = {
    "get_project_overview": set(),
    "get_daily_reports": {"start_date", "end_date", "limit"},
    "search_project_documents": {"question"},
    "get_ml_predictions": {"model_key"},
    "get_expense_summary": {"start_date", "end_date", "category"},
    "compare_project_progress": {"from_date", "to_date"},
    "generate_project_summary": set(),
}
_REQUIRED_ARGUMENTS = {
    "search_project_documents": {"question"},
    "compare_project_progress": {"from_date", "to_date"},
}


class ToolArgumentError(ValueError):
    """A model tool call had missing, unexpected, or invalid arguments."""


def _validate_date(value: Any, name: str, *, required: bool = False) -> date | None:
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise ToolArgumentError(f"{name} must be a YYYY-MM-DD date.")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise ToolArgumentError(f"{name} must be a YYYY-MM-DD date.") from error
    if parsed.isoformat() != value:
        raise ToolArgumentError(f"{name} must be a YYYY-MM-DD date.")
    return parsed


def _validated_args(name: str, arguments: Any) -> dict:
    if name not in _ARGUMENT_KEYS:
        raise ToolArgumentError("This agent tool is not available.")
    if not isinstance(arguments, dict):
        raise ToolArgumentError("Tool arguments must be an object.")
    if set(arguments) - _ARGUMENT_KEYS[name]:
        raise ToolArgumentError("Tool arguments contain unsupported fields.")
    if _REQUIRED_ARGUMENTS.get(name, set()) - set(arguments):
        raise ToolArgumentError("Tool arguments are missing required fields.")

    if name == "get_daily_reports":
        start = _validate_date(arguments.get("start_date"), "start_date")
        end = _validate_date(arguments.get("end_date"), "end_date")
        if start and end and start > end:
            raise ToolArgumentError("start_date must not be after end_date.")
        limit = arguments.get("limit", 5)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10:
            raise ToolArgumentError("limit must be an integer from 1 to 10.")
    elif name == "search_project_documents":
        question = arguments.get("question")
        if not isinstance(question, str) or not 3 <= len(question.strip()) <= 1000:
            raise ToolArgumentError("question must contain 3 to 1000 characters.")
    elif name == "get_ml_predictions":
        model_key = arguments.get("model_key")
        if model_key is not None and model_key not in MODEL_NAMES:
            raise ToolArgumentError("model_key must identify a supported prediction.")
    elif name == "get_expense_summary":
        start = _validate_date(arguments.get("start_date"), "start_date")
        end = _validate_date(arguments.get("end_date"), "end_date")
        if start and end and start > end:
            raise ToolArgumentError("start_date must not be after end_date.")
        category = arguments.get("category")
        if category is not None and (
            not isinstance(category, str) or not category.strip() or len(category) > 64
        ):
            raise ToolArgumentError("category must contain 1 to 64 characters.")
    elif name == "compare_project_progress":
        start = _validate_date(arguments.get("from_date"), "from_date", required=True)
        end = _validate_date(arguments.get("to_date"), "to_date", required=True)
        if start == end:
            raise ToolArgumentError("Choose two different reporting dates.")

    return arguments


def _date_range(query, column, arguments: dict, first: str = "start_date", last: str = "end_date"):
    start = _validate_date(arguments.get(first), first)
    end = _validate_date(arguments.get(last), last)
    if start:
        query = query.where(column >= start)
    if end:
        query = query.where(column <= end)
    return query


def _project_overview(project_id: int) -> dict:
    project = db.session.get(Project, project_id)
    if project is None:
        return {"found": False}
    report_count = db.session.scalar(
        db.select(func.count()).select_from(DailyConstructionReport).where(
            DailyConstructionReport.project_id == project_id
        )
    )
    document_count = db.session.scalar(
        db.select(func.count()).select_from(Document).where(
            Document.project_id == project_id,
            Document.category.is_not(None),
            Document.processing_status == "completed",
        )
    )
    expense_count = db.session.scalar(
        db.select(func.count()).select_from(Expense).where(Expense.project_id == project_id)
    )
    prediction_count = db.session.scalar(
        db.select(func.count()).select_from(DocumentPrediction)
        .join(Document, Document.id == DocumentPrediction.document_id)
        .where(Document.project_id == project_id)
    )
    return {
        "found": True,
        "name": project.name,
        "location": project.location,
        "description": project.description[:1000] if project.description else None,
        "status": project.status,
        "start_date": project.start_date.isoformat() if project.start_date else None,
        "target_end_date": project.target_end_date.isoformat() if project.target_end_date else None,
        "report_count": report_count,
        "processed_document_count": document_count,
        "expense_record_count": expense_count,
        "saved_prediction_count": prediction_count,
    }


def _daily_reports(project_id: int, arguments: dict) -> dict:
    query = db.select(DailyConstructionReport).where(
        DailyConstructionReport.project_id == project_id
    )
    query = _date_range(query, DailyConstructionReport.reporting_date, arguments)
    limit = arguments.get("limit", 5)
    count_query = db.select(func.count()).select_from(query.subquery())
    reports = db.session.scalars(
        query.order_by(DailyConstructionReport.reporting_date.desc()).limit(limit)
    ).all()
    return {
        "total_matching": db.session.scalar(count_query),
        "reports": [
            {
                "id": report.id,
                "reporting_date": report.reporting_date.isoformat(),
                "report_text": report.report_text[:2000],
                "fields": _compact_report_fields(report.fields),
                "source_documents": [
                    {"document_id": source.id, "file_name": source.file_name}
                    for source in report.source_documents
                ],
            }
            for report in reports
        ],
    }


def _search_documents(project_id: int, arguments: dict) -> dict:
    from siteintel.documents.retrieval import search_project_documents

    question = arguments["question"].strip()
    result = search_project_documents(project_id, question, top_k=5)
    return {
        "question": question,
        "status": (
            "no_documents" if result.indexed_documents == 0
            else "found" if result.sufficient else "no_results"
        ),
        "indexed_documents": result.indexed_documents,
        "sources": [
            {
                "number": index,
                "document_id": chunk.document_id,
                "document_name": chunk.document_name,
                "category": chunk.category,
                "page_number": chunk.page_number,
                "reporting_date": chunk.reporting_date,
                "excerpt": chunk.excerpt,
            }
            for index, chunk in enumerate(
                (chunk for chunk in result.chunks if result.sufficient), start=1
            )
        ],
    }


def _compact_report_fields(fields: Any) -> dict:
    if not isinstance(fields, dict):
        return {}
    compact = {"sections": [], "sources": []}
    sections = fields.get("sections", [])
    sources = fields.get("sources", [])
    for section in sections[:12] if isinstance(sections, list) else []:
        if not isinstance(section, dict):
            continue
        items = []
        section_items = section.get("items", [])
        for item in section_items[:30] if isinstance(section_items, list) else []:
            if not isinstance(item, dict):
                continue
            label, value = item.get("label"), item.get("value")
            if isinstance(label, str) and isinstance(value, str):
                items.append({
                    "label": label[:200],
                    "value": value[:500],
                    "source_name": (
                        item["source_name"][:255]
                        if isinstance(item.get("source_name"), str) else None
                    ),
                })
        compact["sections"].append({
            "name": section.get("name", "")[:100]
            if isinstance(section.get("name"), str) else "",
            "items": items,
        })
    for source in sources[:20] if isinstance(sources, list) else []:
        if isinstance(source, dict):
            compact["sources"].append({
                "document_id": source.get("document_id"),
                "file_name": source.get("file_name", "")[:255]
                if isinstance(source.get("file_name"), str) else "",
            })
    return compact


def _ml_predictions(project_id: int, arguments: dict) -> dict:
    query = (
        db.select(DocumentPrediction)
        .join(PredictionHead, PredictionHead.prediction_id == DocumentPrediction.id)
        .join(Document, Document.id == DocumentPrediction.document_id)
        .where(Document.project_id == project_id)
    )
    if arguments.get("model_key"):
        query = query.where(DocumentPrediction.model_key == arguments["model_key"])
    records = db.session.scalars(query.order_by(DocumentPrediction.id.desc()).limit(30)).all()
    return {
        "predictions": [
            {
                **serialize_prediction(record),
                "model_name": MODEL_NAMES.get(record.model_key, record.model_key),
            }
            for record in records
        ],
        "note": "Only existing current prediction-head records are returned; no model was run.",
    }


def _expense_summary(project_id: int, arguments: dict) -> dict:
    expense_query = db.select(Expense).where(Expense.project_id == project_id)
    expense_query = _date_range(expense_query, Expense.expense_date, arguments)
    category = arguments.get("category")
    if category:
        expense_query = expense_query.where(Expense.category == category)

    totals_query = (
        db.select(
            Expense.currency,
            Expense.category,
            func.sum(Expense.amount),
            func.sum(Expense.paid_amount),
            func.sum(Expense.amount - Expense.paid_amount),
        )
        .where(Expense.project_id == project_id)
    )
    totals_query = _date_range(totals_query, Expense.expense_date, arguments)
    if category:
        totals_query = totals_query.where(Expense.category == category)
    totals_query = totals_query.group_by(Expense.currency, Expense.category)

    status_query = (
        db.select(Expense.status, func.count())
        .where(Expense.project_id == project_id)
    )
    status_query = _date_range(status_query, Expense.expense_date, arguments)
    if category:
        status_query = status_query.where(Expense.category == category)

    bill_status = case(
        (Expense.id.is_(None), "unlinked"),
        (Expense.paid_amount <= 0, "unpaid"),
        (Expense.paid_amount >= Expense.amount, "paid"),
        else_="partial",
    )
    bills_query = (
        db.select(bill_status, func.count())
        .select_from(ExpenseBill)
        .outerjoin(Expense, Expense.id == ExpenseBill.expense_id)
        .where(ExpenseBill.project_id == project_id)
    )
    bills_query = _date_range(bills_query, Expense.expense_date, arguments)
    if category:
        bills_query = bills_query.where(Expense.category == category)

    observations_query = (
        db.select(
            FinancialObservation.kind,
            FinancialObservation.currency,
            FinancialObservation.category,
            func.sum(FinancialObservation.amount),
        )
        .join(Document, Document.id == FinancialObservation.document_id)
        .where(
            FinancialObservation.project_id == project_id,
            FinancialObservation.status == "confirmed",
            Document.project_id == project_id,
            Document.processing_status == "completed",
        )
    )
    observations_query = _date_range(
        observations_query, FinancialObservation.observed_date, arguments
    )
    if category:
        observations_query = observations_query.where(
            FinancialObservation.category == category
        )
    observations_query = observations_query.group_by(
        FinancialObservation.kind,
        FinancialObservation.currency,
        FinancialObservation.category,
    )

    expense_rows = db.session.scalars(
        expense_query.order_by(Expense.expense_date.desc(), Expense.id.desc()).limit(20)
    ).all()
    source_query = db.select(
        FinancialObservation.document_id,
        Document.file_name,
        FinancialObservation.page_number,
        FinancialObservation.kind,
        FinancialObservation.category,
        FinancialObservation.label,
        FinancialObservation.amount,
        FinancialObservation.currency,
        FinancialObservation.observed_date,
        FinancialObservation.source_text,
    ).join(Document, Document.id == FinancialObservation.document_id).where(
        FinancialObservation.project_id == project_id,
        FinancialObservation.status == "confirmed",
        Document.project_id == project_id,
        Document.category.is_not(None),
        Document.processing_status == "completed",
    )
    source_query = _date_range(
        source_query, FinancialObservation.observed_date, arguments
    )
    if category:
        source_query = source_query.where(FinancialObservation.category == category)
    source_rows = db.session.execute(
        source_query
        .order_by(FinancialObservation.id.desc())
        .limit(20)
    ).all()
    bill_rows_query = (
        db.select(ExpenseBill, Expense)
        .outerjoin(Expense, Expense.id == ExpenseBill.expense_id)
        .where(ExpenseBill.project_id == project_id)
    )
    bill_rows_query = _date_range(bill_rows_query, Expense.expense_date, arguments)
    if category:
        bill_rows_query = bill_rows_query.where(Expense.category == category)
    bill_rows = db.session.execute(
        bill_rows_query.order_by(ExpenseBill.created_at.desc(), ExpenseBill.id.desc()).limit(20)
    ).all()
    return {
        "filters": {
            "start_date": arguments.get("start_date"),
            "end_date": arguments.get("end_date"),
            "category": category,
        },
        "expense_totals": [
            {
                "currency": currency,
                "category": expense_category,
                "amount": str(amount or Decimal("0")),
                "paid_amount": str(paid or Decimal("0")),
                "unpaid_amount": str(unpaid or Decimal("0")),
            }
            for currency, expense_category, amount, paid, unpaid
            in db.session.execute(totals_query)
        ],
        "expense_status_counts": [
            {"status": status, "count": count}
            for status, count in db.session.execute(
                status_query.group_by(Expense.status)
            )
        ],
        "uploaded_bill_payment_counts": [
            {"payment_status": status, "count": count}
            for status, count in db.session.execute(
                bills_query.group_by(bill_status)
            )
        ],
        "recent_bills": [
            {
                "file_name": bill.file_name,
                "uploaded_at": bill.created_at.isoformat() if bill.created_at else None,
                "linked_expense": (
                    {
                        "description": expense.description[:500],
                        "category": expense.category,
                        "amount": str(expense.amount),
                        "paid_amount": str(expense.paid_amount),
                        "currency": expense.currency,
                        "payment_status": expense.payment_status,
                    }
                    if expense is not None else None
                ),
            }
            for bill, expense in bill_rows
        ],
        "confirmed_document_financial_totals": [
            {
                "kind": kind,
                "currency": currency,
                "category": expense_category,
                "amount": str(amount or Decimal("0")),
            }
            for kind, currency, expense_category, amount
            in db.session.execute(observations_query)
        ],
        "sources": [
            {
                "document_id": document_id,
                "document_name": file_name,
                "page_number": page_number,
                "kind": kind,
                "category": observation_category,
                "label": label,
                "amount": str(amount),
                "currency": currency,
                "observed_date": observed_date.isoformat() if observed_date else None,
                "excerpt": source_text[:500],
            }
            for (
                document_id, file_name, page_number, kind, observation_category,
                label, amount, currency, observed_date, source_text,
            ) in source_rows
        ],
        "recent_expenses": [
            {
                "date": item.expense_date.isoformat(),
                "category": item.category,
                "description": item.description[:500],
                "vendor": item.vendor[:160] if item.vendor else None,
                "amount": str(item.amount),
                "paid_amount": str(item.paid_amount),
                "unpaid_amount": str(item.amount - item.paid_amount),
                "currency": item.currency,
                "status": item.status,
                "payment_status": item.payment_status,
                "has_bill": item.bill is not None,
            }
            for item in expense_rows
        ],
        "recent_expense_limit": 20,
    }


def _flatten_report(fields: Any) -> dict[str, str]:
    if not isinstance(fields, dict):
        return {}
    flattened = {}
    sections = fields.get("sections", [])
    for section in sections[:50] if isinstance(sections, list) else []:
        if not isinstance(section, dict):
            continue
        section_name = str(section.get("name", ""))[:100]
        items = section.get("items", [])
        for item in items[:100] if isinstance(items, list) else []:
            if isinstance(item, dict):
                label, value = item.get("label"), item.get("value")
                if isinstance(label, str) and isinstance(value, str):
                    flattened[f"{section_name}: {label[:200]}"] = value[:500]
    return flattened


def _compare_progress(project_id: int, arguments: dict) -> dict:
    first_date = _validate_date(arguments["from_date"], "from_date", required=True)
    second_date = _validate_date(arguments["to_date"], "to_date", required=True)
    reports = db.session.scalars(
        db.select(DailyConstructionReport).where(
            DailyConstructionReport.project_id == project_id,
            DailyConstructionReport.reporting_date.in_((first_date, second_date)),
        )
    ).all()
    by_date = {report.reporting_date: report for report in reports}
    first = by_date.get(first_date)
    second = by_date.get(second_date)
    if first is None or second is None:
        return {
            "from_date": first_date.isoformat(),
            "to_date": second_date.isoformat(),
            "status": "report_missing",
            "available_dates": [day.isoformat() for day in by_date],
        }
    before = _flatten_report(first.fields)
    after = _flatten_report(second.fields)
    return {
        "from_date": first_date.isoformat(),
        "to_date": second_date.isoformat(),
        "status": "compared",
        "changed": [
            {"field": key, "from": before[key], "to": after[key]}
            for key in sorted(before.keys() & after.keys())
            if before[key] != after[key]
        ],
        "added": [
            {"field": key, "value": after[key]}
            for key in sorted(after.keys() - before.keys())
        ],
        "not_in_later_report": sorted(before.keys() - after.keys()),
        "from_report_id": first.id,
        "to_report_id": second.id,
    }


def execute_tool(project_id: int, name: str, arguments: Any) -> dict:
    """Validate a model-requested tool call and execute one read-only action."""
    arguments = _validated_args(name, arguments)
    if name == "get_project_overview":
        return _project_overview(project_id)
    if name == "get_daily_reports":
        return _daily_reports(project_id, arguments)
    if name == "search_project_documents":
        return _search_documents(project_id, arguments)
    if name == "get_ml_predictions":
        return _ml_predictions(project_id, arguments)
    if name == "get_expense_summary":
        return _expense_summary(project_id, arguments)
    if name == "compare_project_progress":
        return _compare_progress(project_id, arguments)
    if name == "generate_project_summary":
        return {
            "project": _project_overview(project_id),
            "recent_reports": _daily_reports(project_id, {"limit": 5})["reports"],
            "current_predictions": _ml_predictions(project_id, {})["predictions"],
            "expenses": _expense_summary(project_id, {}),
        }
    raise ToolArgumentError("This agent tool is not available.")
