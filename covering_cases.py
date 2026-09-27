import re
from collections import OrderedDict


EXPLICIT_COVERING_PATTERNS = [
    re.compile(r"covering(?:\s+(?:no|number|num))?(?:\s*[_#-]?\s*)(\d{3,})", re.IGNORECASE),
    re.compile(r"covering\s*#\s*(\d{3,})", re.IGNORECASE),
]

APPROVAL_PATTERNS = [
    re.compile(r"(?<![\w/])(\d{3,})(?=\s+(?:ok|approved|approval|accepted|confirmed|done|clear|complete|finalized)\b)", re.IGNORECASE),
]


def _extract_number_matches(text: str, patterns):
    matches = []
    seen = set()

    for pattern in patterns:
        for match in pattern.finditer(text):
            value = match.group(1)
            if value not in seen:
                seen.add(value)
                matches.append(value)

    return matches


def _find_reference_to_existing_covering(text: str, known_coverings):
    if not known_coverings:
        return None

    for match in re.finditer(r"(?<![\w/])(\d{3,})(?![\w/])", text):
        candidate = match.group(1)
        if candidate in known_coverings:
            return candidate

    return None


def extract_covering_cases(messages):
    """Group messages by a deterministic covering number."""
    cases = OrderedDict()

    for message in messages:
        text = (message.get("text") or "").strip()
        if not text:
            continue

        detected_numbers = _extract_number_matches(text, EXPLICIT_COVERING_PATTERNS + APPROVAL_PATTERNS)

        if not detected_numbers:
            reference = _find_reference_to_existing_covering(text, set(cases.keys()))
            if reference:
                detected_numbers = [reference]

        if not detected_numbers:
            continue

        for covering_number in detected_numbers:
            if covering_number not in cases:
                cases[covering_number] = {
                    "covering_number": covering_number,
                    "messages": [],
                    "participants": [],
                    "first_seen": None,
                    "last_seen": None,
                }

            case = cases[covering_number]
            case["messages"].append(dict(message))

            sender = message.get("sender")
            if sender and sender not in case["participants"]:
                case["participants"].append(sender)

            timestamp = message.get("timestamp")
            if timestamp:
                if case["first_seen"] is None or timestamp < case["first_seen"]:
                    case["first_seen"] = timestamp
                if case["last_seen"] is None or timestamp > case["last_seen"]:
                    case["last_seen"] = timestamp

    return [
        {
            "covering_number": case["covering_number"],
            "messages": case["messages"],
            "participants": case["participants"],
            "first_seen": case["first_seen"],
            "last_seen": case["last_seen"],
        }
        for case in cases.values()
    ]
    