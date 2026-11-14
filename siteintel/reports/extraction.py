import re


_LABEL_PATTERN = re.compile(
    r"^\s*(?P<label>[A-Za-z0-9][A-Za-z0-9 _./°³^()-]{0,80}?)\s*[:=]\s*(?P<value>.*?)\s*$"
)

# Unit spellings per model unit, with the factor that converts to that unit.
_END = r"(?![A-Za-z0-9²³])"
_UNIT_PATTERNS = {
    "tonnes": (
        (re.compile(r"(?:metric\s+)?(?:tonnes?|tons?|t)" + _END, re.I), 1.0),
        (re.compile(r"kg" + _END, re.I), 0.001),
    ),
    "mm": (
        (re.compile(r"mm" + _END, re.I), 1.0),
        (re.compile(r"cm" + _END, re.I), 10.0),
        (re.compile(r"(?:inch(?:es)?|in)" + _END, re.I), 25.4),
    ),
    "°C": (
        (re.compile(r"(?:°\s*C|℃|degrees?\s+C(?:elsius)?|celsius|C)" + _END, re.I), 1.0),
        (re.compile(r"(?:°\s*F|℉|degrees?\s+F(?:ahrenheit)?|fahrenheit|F)" + _END, re.I), None),
    ),
    "kg/m³": (
        (re.compile(r"kg\s*(?:/|per)\s*(?:cu\.?\s*)?m(?:3|³|\^\s*3|\?)?" + _END, re.I), 1.0),
    ),
    "days": (
        (re.compile(r"days?" + _END, re.I), 1.0),
        (re.compile(r"weeks?" + _END, re.I), 7.0),
    ),
}
_NUMBER = r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_SEPARATOR = r"\s*(?:[:=\-–]\s*)?(?:is\s+)?"
_alias_cache = {}


def _convert(unit_kind, number, unit_text):
    for pattern, factor in _UNIT_PATTERNS[unit_kind]:
        if pattern.fullmatch(unit_text.strip()):
            if factor is None:
                return (number - 32) * 5 / 9
            if unit_kind == "kg/m³" and re.fullmatch(r"kg\s*/\s*m", unit_text.strip(), re.I):
                return None
            return number * factor
    return None


def _alias_patterns():
    if not _alias_cache:
        from siteintel.ml.document_predictions import ALIASES

        for feature, aliases in ALIASES.items():
            for alias in aliases:
                words = [re.escape(word) for word in re.split(r"[\s_.-]+", alias) if word]
                _alias_cache[(feature, alias)] = re.compile(
                    r"^[\s|•*\-–]*" + r"[\s_.\-]+".join(words) + r"(?![A-Za-z0-9])", re.I
                )
    return _alias_cache


def _scan_line(line):
    """Find a model feature written as a table row or sentence start, e.g.
    "Rainfall 12 mm (forecast)" or "Weather risk Medium Cloudy ...".

    Numeric features require a recognized unit so that "Cement 120 50 35 bags"
    or "Cement purchase and delivery 32,500" are not read as measurements.
    """
    from siteintel.ml.document_predictions import FIELDS

    line = line.replace("|", " ")
    for (feature, _), pattern in sorted(
        _alias_patterns().items(), key=lambda item: -len(item[0][1])
    ):
        match = pattern.match(line)
        if not match:
            continue
        name, unit, _, options = FIELDS[feature]
        rest = line[match.end():]
        bracket = re.match(r"\s*\(([^()]*)\)", rest)
        bracket_unit = bracket.group(1).strip() if bracket else ""
        if bracket:
            rest = rest[bracket.end():]
        rest = re.sub(r"^" + _SEPARATOR, "", rest)
        if options:
            word = re.match(r"([A-Za-z]+)(?![A-Za-z0-9])", rest)
            canonical = next(
                (item for item in options if word and item.casefold() == word.group(1).casefold()),
                None,
            )
            if canonical:
                return feature, name, canonical
            continue
        number = re.match(r"(" + _NUMBER + r")\s*(.*)$", rest)
        if not number:
            continue
        try:
            value = float(number.group(1).replace(",", ""))
        except ValueError:
            continue
        unit_text = number.group(2).strip() or bracket_unit
        converted = None
        for token in (unit_text, bracket_unit):
            for end in range(len(token), 0, -1):
                if end == len(token) or re.match(r"[\s(,;.]", token[end]):
                    converted = _convert(unit, value, token[:end])
                    if converted is not None:
                        break
            if converted is not None:
                break
        if converted is None:
            continue
        return feature, f"{name} ({unit})", f"{converted:.10g}"
    return None


def extract_line_fields(line: str) -> dict[str, str]:
    """Fields on one line. An explicit `label: value` whose label names a model
    feature keeps its own label; otherwise table-style model rows are scanned."""
    from siteintel.ml.document_predictions import _feature

    match = _LABEL_PATTERN.match(line)
    if match:
        label, value = match.group("label").strip(), match.group("value").strip()
        if label and value and _feature(label) is not None:
            return {label: value}
    scanned = _scan_line(line)
    if scanned:
        return {scanned[1]: scanned[2]}
    if match:
        label, value = match.group("label").strip(), match.group("value").strip()
        if label and value:
            return {label: value}
    return {}


def extract_report_fields(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in text.splitlines():
        for label, value in extract_line_fields(line).items():
            fields.setdefault(label, value)
    return fields
