"""Text cleaning and page-aware chunking."""
import re
from collections import Counter

CHUNK_MAX_CHARS = 1000
CHUNK_OVERLAP_CHARS = 150
MIN_CHUNK_CHARS = 30


def clean_text(text: str) -> str:
    """Normalise extracted text without changing its meaning."""
    text = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u00a0", " ").replace("\ufeff", "")
    text = re.sub(r"(\w)-\n(?=[a-z])", r"\1", text)  # re-join hyphenated line breaks
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def strip_repeated_lines(pages: list[str]) -> list[str]:
    """Drop short header/footer lines that repeat on most pages."""
    if len(pages) < 3:
        return pages
    counts: Counter[str] = Counter()
    for page in pages:
        counts.update({line.strip() for line in page.split("\n") if line.strip()})
    threshold = max(3, int(len(pages) * 0.6))
    repeated = {
        line for line, count in counts.items()
        if count >= threshold and len(line) <= 80 and not re.search(r"\d{2,}[\d,.]*\s*(?:%|tonnes|mpa)", line.lower())
    }
    if not repeated:
        return pages
    return ["\n".join(line for line in page.split("\n") if line.strip() not in repeated) for page in pages]


def _units(text: str, limit: int) -> list[str]:
    """One unit per non-empty line; over-long lines are split at spaces."""
    units: list[str] = []
    for line in text.split("\n"):
        line = line.strip()
        while len(line) > limit:
            cut = line.rfind(" ", 0, limit)
            cut = cut if cut > limit // 2 else limit
            units.append(line[:cut].strip())
            line = line[cut:].strip()
        if line:
            units.append(line)
    return units


def chunk_page(text: str, max_chars: int = CHUNK_MAX_CHARS, overlap: int = CHUNK_OVERLAP_CHARS) -> list[str]:
    """Pack whole sentences into overlapping chunks that never leave the page."""
    text = text.strip()
    if len(text) < MIN_CHUNK_CHARS:
        return [text] if text else []
    chunks: list[str] = []
    current: list[str] = []
    length = 0
    for unit in _units(text, max_chars):
        if current and length + len(unit) + 1 > max_chars:
            chunks.append(" ".join(current))
            carried: list[str] = []
            carried_length = 0
            for previous in reversed(current):
                if carried_length + len(previous) > overlap:
                    break
                carried.insert(0, previous)
                carried_length += len(previous) + 1
            current, length = carried, carried_length
        current.append(unit)
        length += len(unit) + 1
    if current:
        last = " ".join(current)
        # A trailing chunk made only of carried-over overlap adds nothing.
        if not chunks or last not in chunks[-1]:
            chunks.append(last)
    return chunks
