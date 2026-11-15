"""Local sparse embeddings.

Each chunk is embedded as a hashed bag of terms (unigrams and adjacent-term
bigrams). The mapping is stateless and deterministic, so a chunk can be
embedded once at upload time and scored later against any question without
refitting a vocabulary and without any external service.
"""
import re
import zlib
from collections import Counter

EMBEDDING_MODEL = "hashed-terms-v1"
DIMENSIONS = 1 << 20

STOPWORDS = frozenset("""
a an and are as at be been being but by can could did do does for from had has have how i if in into is it its
of on or our over shall should so than that the their them then there these they this those to under up was we
were what when where which who whom why will with would you your about above after again all also any because
before between both each few more most other out same some such too very
mentioned mention mentions specified specify specifies stated state states document documents uploaded project
projects please tell give show find describe explain provide according given
""".split())

_TOKEN = re.compile(r"[a-z0-9]+(?:[._/\-][a-z0-9]+)*")


def _stem(token: str) -> str:
    if not token.isalpha() or len(token) <= 4:
        return token
    if token.endswith("ies"):
        return token[:-3] + "y"
    for suffix in ("ing", "ed"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 4:
            token = token[: -len(suffix)]
            break
    else:
        if token.endswith("s") and not token.endswith("ss"):
            token = token[:-1]
    return token[:-1] if token.endswith("e") and len(token) > 4 else token


def tokenize(text: str) -> list[str]:
    """Lower-case terms; compound tokens like ``m-25`` also yield their parts."""
    terms: list[str] = []
    for raw in _TOKEN.findall(text.lower()):
        parts = re.split(r"[._/\-]", raw)
        candidates = [raw] if len(parts) == 1 else [raw, *parts]
        for candidate in candidates:
            if candidate and candidate not in STOPWORDS:
                terms.append(_stem(candidate))
    return terms


def _bucket(term: str) -> int:
    return zlib.crc32(term.encode("utf-8")) % DIMENSIONS


def term_counts(text: str) -> tuple[Counter[int], Counter[int], int]:
    """Return (unigram buckets, bigram buckets, token count)."""
    tokens = tokenize(text)
    unigrams = Counter(_bucket(token) for token in tokens)
    bigrams = Counter(_bucket(f"{a} {b}") for a, b in zip(tokens, tokens[1:]))
    return unigrams, bigrams, len(tokens)


def embed(text: str) -> tuple[dict[str, list], int]:
    """Embed text as ``{"i": [...], "v": [...]}`` plus its token count."""
    unigrams, bigrams, length = term_counts(text)
    merged = Counter(unigrams)
    merged.update(bigrams)
    indices = sorted(merged)
    return {"i": indices, "v": [merged[index] for index in indices]}, length
