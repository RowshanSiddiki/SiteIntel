"""Extract conservative, reviewable financial observations from indexed pages."""
import hashlib
import re
from datetime import date, datetime
from decimal import Decimal

from siteintel.extensions import db
from siteintel.projects.models import FinancialObservation

_AMOUNT = re.compile(
    r"(?<!\w)(?:(BDT|USD|EUR|GBP|INR|৳|₹|€|£)\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)|"
    r"([0-9][0-9,]*(?:\.[0-9]{1,2})?)\s*(BDT|USD|EUR|GBP|INR|৳|₹|€|£))(?!\w)", re.I
)
_SYMBOLS = {"৳": "BDT", "₹": "INR", "€": "EUR", "£": "GBP"}
_CATEGORIES = {
    "Materials": re.compile(r"\b(materials?|cement|concrete|steel|sand|bricks?|aggregate|timber)\b", re.I),
    "Labour": re.compile(r"\b(labou?r|wages?|workforce)\b", re.I),
    "Equipment": re.compile(r"\b(equipment|machinery|excavator|crane)\b", re.I),
    "Transportation": re.compile(r"\b(transport|transportation|freight|delivery)\b", re.I),
    "Utilities": re.compile(r"\b(utilit(?:y|ies)|electricity|water)\b", re.I),
    "Other": re.compile(r"\b(?:other|miscellaneous)\s+(?:cost|expense|charge)\b", re.I),
}
_DATE = re.compile(
    r"(?im)^\s*(?:quotation|invoice|bill|receipt|reporting)?\s*date\s*[:\-]?\s*"
    r"(\d{4}-\d{2}-\d{2}|\d{1,2}\s+[A-Za-z]+\s+\d{4})\b"
)
_PAYMENT_DATE = re.compile(
    r"(?im)^\s*(?:payment|paid)\s+date\s*[:\-]?\s*"
    r"(\d{4}-\d{2}-\d{2}|\d{1,2}\s+[A-Za-z]+\s+\d{4})\b"
)
_REFERENCE = re.compile(
    r"(?im)^\s*(invoice|bill|receipt|quotation)\s*(?:number|no\.?|#)\s*[:#]?\s*([A-Za-z0-9][A-Za-z0-9/-]{1,80})"
)
_VENDOR = re.compile(r"(?im)^\s*(?:vendor|supplier|seller)\s*:\s*([^\n]{2,100})$")


def _category(line):
    matches = [name for name, pattern in _CATEGORIES.items() if pattern.search(line)]
    return matches[0] if len(matches) == 1 else "Unclassified"


def _document_date(text, reporting_date, pattern=_DATE):
    if reporting_date:
        return reporting_date
    dates = set()
    for match in pattern.finditer(text):
        for fmt in ("%Y-%m-%d", "%d %B %Y", "%d %b %Y"):
            try:
                dates.add(datetime.strptime(match.group(1), fmt).date())
                break
            except ValueError:
                continue
    return next(iter(dates)) if len(dates) == 1 else None


def extract_observations(pages, *, reporting_date=None, document_category=None):
    text = "\n".join(page.text for page in pages)
    observed_date = _document_date(text, reporting_date)
    payment_date = _document_date(text, None, _PAYMENT_DATE)
    reference = _REFERENCE.search(text)
    vendor = _VENDOR.search(text)
    document_reference = (
        hashlib.sha256(
            f"{reference.group(1).lower()}|{reference.group(2).upper()}|"
            f"{' '.join(vendor.group(1).casefold().split())}".encode()
        ).hexdigest() if reference and vendor else None
    )
    invoice = bool(re.search(r"(?im)^\s*(?:tax\s+)?(?:invoice|receipt|bill)(?:\s+(?:no\.?|number|#)\b|[\s:]*$)", text))
    quotation = document_category == "quotation" or (
        not invoice and bool(re.search(
            r"(?im)^\s*(?:(?:material|supplier|contractor)\s+)?quotation\b", text
        ))
    )
    invoice = invoice and not quotation
    candidates = []
    previous_line = ""
    for page in pages:
        in_table = False
        for raw_line in page.text.splitlines():
            line = " ".join(raw_line.split())
            if re.search(r"\bmaterial\b.*\bunit price\b.*\btotal\b", line, re.I):
                in_table = True
                previous_line = ""
                continue
            if re.match(r"^(?:subtotal|summary|delivery|grand total|total)\b", line, re.I):
                in_table = False
            amounts = list(_AMOUNT.finditer(line))
            label_line = line
            if in_table and len(amounts) == 2:
                label_line = f"{previous_line} {line}".strip() if _category(line) == "Unclassified" else line
            elif len(amounts) != 1:
                previous_line = line
                continue
            if len(line) > 2000:
                continue
            match = amounts[-1]
            currency = (match.group(1) or match.group(4)).upper()
            currency = _SYMBOLS.get(currency, currency)
            raw = match.group(2) or match.group(3)
            if not re.fullmatch(r"(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d{1,2})?", raw):
                continue
            amount = Decimal(raw.replace(",", ""))
            if amount <= 0 or amount > Decimal("999999999999.99"):
                continue
            if re.search(r"\b(?:balance due|outstanding(?: amount| balance)?|amount unpaid)\b", line, re.I):
                kind = "outstanding"
            elif re.search(r"\b(?:budget|budgeted)\b", line, re.I):
                kind = "budget"
            elif re.search(r"\b(?:amount paid|payment received|payment made|paid amount|paid in full)\b", line, re.I) or (
                re.search(r"\bpaid\b", line, re.I) and re.search(r"\b(?:payment|advance|deposit)\b", line, re.I)
            ):
                kind = "actual_payment"
            elif re.search(r"\b(?:advance|deposit)\s+payment\b", line, re.I):
                kind = "advance_schedule"
            elif re.search(r"\bbalance\s+payment\b", line, re.I):
                kind = "balance_schedule"
            elif re.match(r"^\s*(?:grand\s+total|total(?:\s+(?:amount|cost|due))?)\b", line, re.I):
                kind = "quotation_total" if quotation else "invoice_total" if invoice else None
            elif re.search(r"\b(?:delivery|freight|transport(?:ation)?)\s+charge\b", line, re.I):
                kind = "delivery_charge"
            elif in_table and len(amounts) == 2 and _category(label_line) == "Materials":
                kind = "material_component"
            elif re.search(r"\b(?:estimated?|forecast|projected|quotation|quoted)\b", line, re.I):
                kind = None
            elif re.search(r"\b(?:cost|expense|spent|expenditure|charge|wages?)\b", line, re.I):
                kind = "material_component" if quotation or invoice else "direct_expense"
            else:
                kind = None
            if not kind:
                previous_line = line
                continue
            label = label_line[:label_line.find(match.group(0))].strip(" :-")[:300] or kind.replace("_", " ").title()
            category = "Transportation" if kind == "delivery_charge" else _category(label)
            if kind == "material_component" and in_table:
                material = next((name.title() for name in ("cement", "sand", "bricks", "steel", "concrete", "timber")
                                 if re.search(rf"\b{name}\b", label_line, re.I)), None)
                if material:
                    label = material
            candidates.append(dict(
                kind=kind, category=category, label=label, amount=amount, currency=currency,
                observed_date=payment_date if kind == "actual_payment" else observed_date,
                page_number=page.number, source_text=line,
                document_reference=document_reference,
            ))
            previous_line = line

    totals = [item for item in candidates if item["kind"] in {"quotation_total", "invoice_total"}]
    if len({(item["kind"], item["amount"], item["currency"]) for item in totals}) > 1:
        candidates = [item for item in candidates if item not in totals]
    deduped = {}
    for item in candidates:
        key = (item["kind"], item["category"], item["amount"], item["currency"])
        if item["kind"] in {"material_component", "direct_expense", "actual_payment"}:
            key += (item["label"].casefold(),)
        if key in deduped:
            continue
        payload = "|".join(str(item[key]) for key in (
            "kind", "category", "label", "amount", "currency", "observed_date",
            "page_number", "source_text", "document_reference",
        ))
        item["fingerprint"] = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        deduped[key] = item
    return {item["fingerprint"]: item for item in deduped.values()}


def sync_observations(document, pages, *, reporting_date=None):
    extracted = extract_observations(pages, reporting_date=reporting_date, document_category=document.category)
    existing = {row.fingerprint: row for row in db.session.scalars(
        db.select(FinancialObservation).where(FinancialObservation.document_id == document.id)
    )}
    for fingerprint, row in existing.items():
        if fingerprint not in extracted:
            row.status = "stale"
        elif row.status == "stale":
            row.status = "pending"
            row.reviewed_by_id = None
    for fingerprint, values in extracted.items():
        if fingerprint not in existing:
            db.session.add(FinancialObservation(
                project_id=document.project_id, document_id=document.id, **values,
            ))
