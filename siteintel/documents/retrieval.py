"""Project-scoped retrieval.

``search_project_documents`` is the single entry point for finding evidence.
It only ever reads chunks whose ``project_id`` matches, from documents that
finished processing, and ranks them with BM25 over the stored embeddings.
"""
import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime

from siteintel.documents.embedding import EMBEDDING_MODEL, term_counts, tokenize
from siteintel.extensions import db
from siteintel.projects.models import Document, DocumentChunk
from siteintel.reports.models import DailyConstructionReport

BM25_K1 = 1.4
BM25_B = 0.75
# Share of the question's (idf-weighted) terms the best passage must contain
# before we treat the documents as holding an answer.
MIN_COVERAGE = 0.5
EXCERPT_CHARS = 700
_QUESTION_FILLER = re.compile(r"\b(?:(?:that|this)\s+day|much|many)\b", re.IGNORECASE)
_DATE = re.compile(
    r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}\s+"
    r"(?:january|february|march|april|may|june|july|august|september|october|november|december)"
    r"\s+\d{4})\b",
    re.IGNORECASE,
)
_SUMMARY_WORDS = frozenset(
    "what happened happen occurred occur summary summarize summarise overview "
    "daily day report reports construction site work activity activities".split()
)


@dataclass(frozen=True)
class SourceChunk:
    chunk_id: int
    document_id: int
    document_name: str
    category: str
    page_number: int
    text: str
    excerpt: str
    score: float
    coverage: float
    reporting_date: str | None = None


@dataclass
class SearchResult:
    question: str
    chunks: list[SourceChunk] = field(default_factory=list)
    indexed_documents: int = 0
    sufficient: bool = False


def _excerpt(text: str, question_terms: set[str]) -> str:
    """The sentence window with the most question terms, cut to a readable size."""
    if len(text) <= EXCERPT_CHARS:
        return text
    sentences = re.split(r"(?<=[.!?])\s+|\n", text)
    best, best_score = 0, -1
    for index, sentence in enumerate(sentences):
        score = len(question_terms.intersection(tokenize(sentence)))
        if score > best_score:
            best, best_score = index, score
    start = max(best - 1, 0)
    window = " ".join(sentences[start:best + 2])
    if len(window) > EXCERPT_CHARS:
        positions = [0] + [
            match.start() for match in re.finditer(r"\b\w+", window)
            if set(tokenize(match.group())) & question_terms
        ]
        start = max(
            (min(max(pos - EXCERPT_CHARS // 3, 0), len(window) - EXCERPT_CHARS)
             for pos in positions),
            key=lambda offset: len(question_terms.intersection(
                tokenize(window[offset:offset + EXCERPT_CHARS])
            )),
        )
        end = start + EXCERPT_CHARS
        window = ("…" if start else "") + window[start:end].strip() + ("…" if end < len(window) else "")
    return window


def _query_text_and_dates(question: str) -> tuple[str, set[date]]:
    dates: set[date] = set()

    def remove_date(match):
        value = match.group()
        for fmt in ("%Y-%m-%d", "%d %B %Y"):
            try:
                dates.add(datetime.strptime(value, fmt).date())
                return " "
            except ValueError:
                continue
        return value

    return _QUESTION_FILLER.sub(" ", _DATE.sub(remove_date, question)), dates


def search_project_documents(
    project_id: int,
    question: str,
    *,
    top_k: int = 5,
    document_ids: list[int] | None = None,
) -> SearchResult:
    """Rank the project's indexed passages against ``question``."""
    result = SearchResult(question=question)
    query = (
        db.select(
            DocumentChunk.id, DocumentChunk.document_id, DocumentChunk.page_number,
            DocumentChunk.text, DocumentChunk.token_count, DocumentChunk.embedding,
            Document.file_name, Document.category, DailyConstructionReport.reporting_date,
        )
        .join(Document, Document.id == DocumentChunk.document_id)
        .outerjoin(
            DailyConstructionReport,
            (DailyConstructionReport.document_id == Document.id)
            & (DailyConstructionReport.project_id == Document.project_id),
        )
        .where(
            DocumentChunk.project_id == project_id,
            Document.project_id == project_id,
            Document.category.is_not(None),
            Document.processing_status == "completed",
            DocumentChunk.embedding_model == EMBEDDING_MODEL,
        )
    )
    if document_ids:
        query = query.where(DocumentChunk.document_id.in_(document_ids))
    rows = db.session.execute(query).all()
    result.indexed_documents = len({row.document_id for row in rows})
    if not rows or top_k <= 0:
        return result

    query_text, requested_dates = _query_text_and_dates(question)
    summary_terms = set(tokenize(query_text))
    summary_vocabulary = set(tokenize(" ".join(_SUMMARY_WORDS)))
    is_summary = bool(summary_terms) and summary_terms <= summary_vocabulary and bool(
        re.search(r"\b(?:happened|occurred|summary|summari[sz]e|overview)\b", query_text, re.I)
    )
    if is_summary:
        report_rows = [
            row for row in rows
            if row.reporting_date and (not requested_dates or row.reporting_date in requested_dates)
        ]
        report_rows.sort(key=lambda row: (-row.reporting_date.toordinal(), row.page_number, row.id))
        for row in report_rows[:top_k]:
            result.chunks.append(SourceChunk(
                chunk_id=row.id, document_id=row.document_id, document_name=row.file_name,
                category=row.category, page_number=row.page_number, text=row.text,
                excerpt=_excerpt(row.text, set()), score=1.0, coverage=1.0,
                reporting_date=row.reporting_date.isoformat(),
            ))
        result.sufficient = bool(result.chunks)
        return result
    unigrams, bigrams, _ = term_counts(query_text)
    unigram_terms = set(unigrams)
    query_terms = unigram_terms | set(bigrams)
    if not unigram_terms:
        return result

    vectors = [dict(zip(row.embedding["i"], row.embedding["v"])) for row in rows]
    total = len(rows)
    average_length = sum(row.token_count for row in rows) / total or 1.0
    frequency = {term: sum(1 for vector in vectors if term in vector) for term in query_terms}
    idf = {
        term: math.log(1 + (total - count + 0.5) / (count + 0.5))
        for term, count in frequency.items()
    }
    weight_total = sum(idf[term] for term in unigram_terms) or 1.0

    scored = []
    for row, vector in zip(rows, vectors):
        norm = BM25_K1 * (1 - BM25_B + BM25_B * row.token_count / average_length)
        score = 0.0
        for term in query_terms:
            count = vector.get(term, 0)
            if count:
                score += idf[term] * count * (BM25_K1 + 1) / (count + norm)
        if score <= 0:
            continue
        if row.reporting_date in requested_dates:
            score *= 1.5
        coverage = sum(idf[term] for term in unigram_terms if term in vector) / weight_total
        scored.append((score, coverage, row))
    scored.sort(key=lambda item: (-item[0], item[2].document_id, item[2].page_number))

    selected = []
    seen_documents = set()
    if scored:
        for item in scored:
            if (item[2].document_id not in seen_documents
                    and item[0] >= scored[0][0] * 0.3 and item[1] >= 0.25):
                selected.append(item)
                seen_documents.add(item[2].document_id)
                if len(selected) == top_k:
                    break
        if top_k > 1:
            target = next(
                (item for item in scored
                 if item[2].reporting_date
                 and (not requested_dates or item[2].reporting_date in requested_dates)
                 and item[1] >= MIN_COVERAGE),
                None,
            )
            if target and target not in selected:
                if len(selected) == top_k:
                    selected[-1] = target
                else:
                    selected.append(target)
        for item in scored:
            if len(selected) == top_k:
                break
            if item not in selected:
                selected.append(item)
        selected.sort(key=lambda item: (-item[0], item[2].document_id, item[2].page_number))

    question_tokens = set(tokenize(question))
    for score, coverage, row in selected:
        result.chunks.append(SourceChunk(
            chunk_id=row.id, document_id=row.document_id, document_name=row.file_name,
            category=row.category, page_number=row.page_number, text=row.text,
            excerpt=_excerpt(row.text, question_tokens), score=round(score, 4),
            coverage=round(coverage, 3),
            reporting_date=row.reporting_date.isoformat() if row.reporting_date else None,
        ))
    result.sufficient = bool(result.chunks) and max(c.coverage for c in result.chunks) >= MIN_COVERAGE
    return result
