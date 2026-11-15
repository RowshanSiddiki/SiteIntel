"""Project-scoped retrieval.

``search_project_documents`` is the single entry point for finding evidence.
It only ever reads chunks whose ``project_id`` matches, from documents that
finished processing, and ranks them with BM25 over the stored embeddings.
"""
import math
import re
from dataclasses import dataclass, field

from siteintel.documents.embedding import EMBEDDING_MODEL, term_counts, tokenize
from siteintel.extensions import db
from siteintel.projects.models import Document, DocumentChunk

BM25_K1 = 1.4
BM25_B = 0.75
# Share of the question's (idf-weighted) terms the best passage must contain
# before we treat the documents as holding an answer.
MIN_COVERAGE = 0.5
EXCERPT_CHARS = 420


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
        window = window[:EXCERPT_CHARS].rsplit(" ", 1)[0] + "…"
    return window


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
            Document.file_name, Document.category,
        )
        .join(Document, Document.id == DocumentChunk.document_id)
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
    if not rows:
        return result

    unigrams, bigrams, _ = term_counts(question)
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
        coverage = sum(idf[term] for term in unigram_terms if term in vector) / weight_total
        scored.append((score, coverage, row))
    scored.sort(key=lambda item: (-item[0], item[2].document_id, item[2].page_number))

    question_tokens = set(tokenize(question))
    for score, coverage, row in scored[:top_k]:
        result.chunks.append(SourceChunk(
            chunk_id=row.id, document_id=row.document_id, document_name=row.file_name,
            category=row.category, page_number=row.page_number, text=row.text,
            excerpt=_excerpt(row.text, question_tokens), score=round(score, 4),
            coverage=round(coverage, 3),
        ))
    result.sufficient = bool(result.chunks) and max(c.coverage for c in result.chunks) >= MIN_COVERAGE
    return result
