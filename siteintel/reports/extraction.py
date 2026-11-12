import re


_LABEL_PATTERN = re.compile(
    r"^\s*(?P<label>[A-Za-z][A-Za-z0-9 _./-]{0,60}?)\s*[:=]\s*(?P<value>.*?)\s*$"
)


def extract_report_fields(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in text.splitlines():
        match = _LABEL_PATTERN.match(line)
        if match:
            label = match.group("label").strip()
            value = match.group("value").strip()
            if label and value:
                fields.setdefault(label, value)
    return fields
