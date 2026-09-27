import re
from datetime import datetime


# WhatsApp exports can come in several real-world variants, including bracketed
# timestamps with seconds and narrow spaces, e.g. [25/07/26, 1:24:54 PM] Name: text.
LINE_RE = re.compile(
    r"^\[?(?P<date>\d{1,2}/\d{1,2}/\d{2,4}),\s*(?P<time>\d{1,2}:\d{2}(?::\d{2})?\s*(?:AM|PM)?)\]?(?:\s*-\s*|\s+)(?P<sender>.+?):\s*(?P<text>.*)$",
    re.IGNORECASE,
)

# Older/alternate exports often omit the bracket and use a simple time without seconds.
ALT_LINE_RE = re.compile(
    r"^\[?(?P<date>\d{1,2}/\d{1,2}/\d{2,4}),\s*(?P<time>\d{1,2}:\d{2})(?:\s*\])?\s*(?:-\s*|\s+)(?P<sender>.+?):\s*(?P<text>.*)$",
    re.IGNORECASE,
)


def _parse_timestamp(date_str: str, time_str: str) -> str:
    clean_time = time_str.strip().replace("\u202f", " ").replace("\u00a0", " ")
    date_value = date_str.strip()

    for fmt in (
        "%m/%d/%Y %I:%M:%S %p",
        "%m/%d/%Y %I:%M %p",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %H:%M",
        "%m/%d/%y %I:%M:%S %p",
        "%m/%d/%y %I:%M %p",
        "%m/%d/%y %H:%M:%S",
        "%m/%d/%y %H:%M",
        "%d/%m/%Y %I:%M:%S %p",
        "%d/%m/%Y %I:%M %p",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d/%m/%y %I:%M:%S %p",
        "%d/%m/%y %I:%M %p",
        "%d/%m/%y %H:%M:%S",
        "%d/%m/%y %H:%M",
    ):
        try:
            dt = datetime.strptime(f"{date_value} {clean_time}", fmt)
            return dt.strftime("%Y-%m-%dT%H:%M:%S")
        except ValueError:
            pass

    raise ValueError(f"Unsupported WhatsApp timestamp format: {date_value} {clean_time}")


def parse_whatsapp_export(raw_text: str):
    """Parse a WhatsApp TXT export into a list of message dictionaries."""
    if not raw_text:
        return []

    lines = raw_text.splitlines()
    messages = []
    current = None

    for line in lines:
        if not line.strip():
            if current is not None and current["text"]:
                current["text"] += "\n"
            continue

        match = LINE_RE.match(line)
        if match is None:
            match = ALT_LINE_RE.match(line)

        if match is not None:
            if current is not None:
                messages.append(current)

            date_str = match.group("date")
            time_str = match.group("time")
            sender = match.group("sender").strip()
            text = match.group("text").strip()

            current = {
                "timestamp": _parse_timestamp(date_str, time_str),
                "sender": sender,
                "text": text,
            }
            continue

        if current is not None:
            current["text"] = (current["text"] + "\n" + line.strip()).strip("\n")
        else:
            # Ignore non-message metadata lines such as WhatsApp startup text.
            continue

    if current is not None:
        messages.append(current)

    return messages
