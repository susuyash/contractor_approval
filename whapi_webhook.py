import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv
from flask import Flask, request

from ai_processor import interpret_cases
from covering_cases import extract_covering_cases
from database import enqueue_webhook_message, get_supabase_client, save_case
from webhook_worker import start_worker, wake_worker

load_dotenv()

app = Flask(__name__)
@app.get("/healthz")
def healthz():
    start_worker()
    return {"status": "ok"}, 200
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
            timestamp = datetime.fromtimestamp(timestamp_value, tz=timezone.utc).isoformat(timespec="seconds")
        elif isinstance(timestamp_value, str) and timestamp_value.isdigit():
            timestamp = datetime.fromtimestamp(int(timestamp_value), tz=timezone.utc).isoformat(timespec="seconds")
        else:
            timestamp = str(timestamp_value or "")
    except (TypeError, ValueError):
        timestamp = str(timestamp_value or "")

    return {
        "timestamp": timestamp,
        "sender": sender,
        "text": text,
    }


def _build_case_payload(case: dict[str, Any], interpretation: Any) -> dict[str, Any]:
    messages = case.get("messages", [])
    return {
        "covering_number": case.get("covering_number"),
        "description": getattr(interpretation, "description", None),
        "urgency": getattr(getattr(interpretation, "urgency", None), "value", None),
        "raised_by": (messages[0].get("sender") if messages else None),
        "first_seen": case.get("first_seen"),
        "last_seen": case.get("last_seen"),
        "participants": [participant.model_dump() for participant in getattr(interpretation, "participants", [])],
        "messages": messages,
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

    return interpretations


def _event_key(normalized: dict[str, Any]) -> str:
    message_id = str(normalized.get("message_id") or "").strip()
    identity = message_id or hashlib.sha256(
        json.dumps(normalized, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return f"{normalized.get('chat_id', '')}:{identity}"


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

        pipeline_message = adapt_whapi_message_to_case_pipeline(normalized)
        if pipeline_message is None or not extract_covering_cases([pipeline_message]):
            continue

        client = get_supabase_client()
        enqueue_webhook_message(client, _event_key(normalized), normalized)
        start_worker()
        wake_worker()
        accepted.append(normalized)

    return accepted


@app.post("/webhook/whapi")
def whapi_webhook() -> tuple[str, int]:
    try:
        payload = request.get_json(silent=True)
    except Exception:
        payload = None

    try:
        ingest_whapi_message(payload)
    except Exception:
        logger.error("Could not durably enqueue Whapi webhook payload")
        return "", 503
    return "", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
