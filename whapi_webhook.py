import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv
from flask import Flask, request

from ai_processor import interpret_cases
from covering_cases import extract_covering_cases
from database import get_supabase_client, save_case
from google_sheets import sync_all_to_google_sheets

load_dotenv()

app = Flask(__name__)
SEEN_MESSAGE_IDS: set[str] = set()
logger = logging.getLogger(__name__)


def _get_env_values() -> tuple[str | None, str | None]:
    return os.getenv("WHAPI_GROUP_ID"), os.getenv("WHAPI_CHANNEL_ID")


def _normalize_timestamp(value: Any) -> Any:
    if isinstance(value, (int, float)):
        return value
    if value is None:
        return ""
    return str(value)


def _normalize_message(message: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(message, dict):
        return None

    if message.get("from_me") is True:
        return None

    if message.get("type") != "text":
        return None

    group_id, _ = _get_env_values()
    if not group_id:
        return None

    chat_id = message.get("chat_id")
    if chat_id != group_id:
        return None

    text_payload = message.get("text")
    if not isinstance(text_payload, dict):
        return None

    body = text_payload.get("body")
    if body is None:
        return None

    message_id = message.get("id")
    if message_id is None:
        message_id = ""

    return {
        "message_id": str(message_id),
        "timestamp": _normalize_timestamp(message.get("timestamp")),
        "sender_id": message.get("from"),
        "sender_name": message.get("from_name"),
        "chat_id": chat_id,
        "chat_name": message.get("chat_name"),
        "message": str(body),
        "source": "whapi",
    }


def adapt_whapi_message_to_case_pipeline(normalized: dict[str, Any]) -> dict[str, Any] | None:
    """Convert the Whapi-normalized message into the same structure expected by parser.py / covering_cases.py."""
    if not isinstance(normalized, dict):
        return None

    sender = normalized.get("sender_name") or normalized.get("sender_id") or "unknown"
    text = normalized.get("message") or ""
    timestamp_value = normalized.get("timestamp")
    try:
        if isinstance(timestamp_value, (int, float)):
            timestamp = datetime.fromtimestamp(timestamp_value, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        else:
            timestamp = str(timestamp_value or "")
    except (TypeError, ValueError):
        timestamp = str(timestamp_value or "")

    return {
        "timestamp": timestamp,
        "sender": sender,
        "text": text,
    }


def _is_duplicate_message(message_id: str | None) -> bool:
    if not message_id:
        return False
    if message_id in SEEN_MESSAGE_IDS:
        return True
    SEEN_MESSAGE_IDS.add(message_id)
    return False


def _build_case_payload(case: dict[str, Any], interpretation: Any) -> dict[str, Any]:
    return {
        "covering_number": case.get("covering_number"),
        "description": getattr(interpretation, "description", None),
        "urgency": getattr(getattr(interpretation, "urgency", None), "value", None),
        "first_seen": case.get("first_seen"),
        "last_seen": case.get("last_seen"),
        "participants": [participant.model_dump() for participant in getattr(interpretation, "participants", [])],
        "messages": case.get("messages", []),
    }


def process_whapi_case(normalized: dict[str, Any]) -> list[Any]:
    """Connect a normalized Whapi message to the existing case-processing pipeline."""
    pipeline_message = adapt_whapi_message_to_case_pipeline(normalized)
    if pipeline_message is None:
        return []

    cases = extract_covering_cases([pipeline_message])
    if not cases:
        return []

    interpretations = interpret_cases(cases)
    client = get_supabase_client()
    for case, interpretation in zip(cases, interpretations):
        payload = _build_case_payload(case, interpretation)
        save_case(client, payload, interpretation)
        try:
            sync_all_to_google_sheets()
        except Exception:
            logger.exception("Google Sheets sync failed after saving covering %s", case.get("covering_number"))

    return interpretations


def ingest_whapi_message(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []

    event = payload.get("event")
    if not isinstance(event, dict):
        return []

    if event.get("type") != "messages" or event.get("event") != "post":
        return []

    messages = payload.get("messages")
    if not isinstance(messages, list):
        return []

    accepted: list[dict[str, Any]] = []
    for message in messages:
        normalized = _normalize_message(message)
        if normalized is None:
            continue

        if _is_duplicate_message(normalized.get("message_id")):
            continue

        process_whapi_case(normalized)
        accepted.append(normalized)
        print(json.dumps(normalized, ensure_ascii=False))

    return accepted


@app.post("/webhook/whapi")
def whapi_webhook() -> tuple[str, int]:
    try:
        payload = request.get_json(silent=True)
    except Exception:
        payload = None

    ingest_whapi_message(payload)
    return "", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
