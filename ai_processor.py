import json
import os
from enum import Enum
from typing import Any

from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator

from covering_cases import extract_covering_cases
from parser import parse_whatsapp_export

load_dotenv()


class ParticipantStatus(str, Enum):
    OK = "OK"
    APPROVED = "APPROVED"
    PENDING = "PENDING"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


class OverallStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


class UrgencyLevel(str, Enum):
    URGENT = "URGENT"
    NORMAL = "NORMAL"
    UNKNOWN = "UNKNOWN"


class ParticipantInterpretation(BaseModel):
    person: str = Field(..., min_length=1)
    status: ParticipantStatus
    last_message: str = Field(..., min_length=1)

    @field_validator("person", "last_message")
    @classmethod
    def strip_whitespace(cls, value: str) -> str:
        if value is None:
            return value
        return value.strip()


class CaseInterpretation(BaseModel):
    covering_number: str = Field(..., min_length=1)
    description: str | None = None
    urgency: UrgencyLevel = UrgencyLevel.UNKNOWN
    participants: list[ParticipantInterpretation] = Field(default_factory=list)
    overall_status: OverallStatus = OverallStatus.UNKNOWN

    @field_validator("covering_number")
    @classmethod
    def normalize_covering_number(cls, value: str) -> str:
        value = value.strip()
        return value.lstrip("# ")

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("urgency", mode="before")
    @classmethod
    def normalize_urgency(cls, value):
        if value is None:
            return UrgencyLevel.UNKNOWN
        if isinstance(value, str):
            normalized = value.strip().upper()
            if normalized in {"URGENT", "HIGH"}:
                return UrgencyLevel.URGENT
            if normalized in {"NORMAL", "LOW"}:
                return UrgencyLevel.NORMAL
            if normalized == "UNKNOWN":
                return UrgencyLevel.UNKNOWN
        return value


def get_gemini_model_name() -> str:
    return os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")


def _require_api_key() -> str:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY is not set. Add it to the environment or .env file.")
    return api_key


def _build_prompt(case: dict[str, Any]) -> str:
    covering_number = str(case.get("covering_number", "")).strip()
    messages = case.get("messages", [])

    prompt_lines = [
        "You are reading contractor WhatsApp payment conversations.",
        "Return valid JSON only matching the required schema.",
        "Do not invent facts. If a value is unclear, use null or UNKNOWN.",
        "",
        f"covering_number: {covering_number}",
        "messages:",
    ]

    for message in messages:
        sender = message.get("sender", "unknown")
        timestamp = message.get("timestamp", "")
        text = message.get("text", "")
        prompt_lines.append(f"- {timestamp} | {sender}: {text}")

    prompt_lines.extend(
        [
            "",
            "Extract:",
            "- covering_number: the covering identifier",
            "- description: concise description of the contractor payment; return null if not clear",
            "- urgency: URGENT, NORMAL, or UNKNOWN",
            "- participants: each person who sent relevant messages, with status and last_message",
            "- overall_status: APPROVED, PENDING, REJECTED, or UNKNOWN",
            "",
            "Rules:",
            "- Use only the supplied messages.",
            "- Do not invent participants.",
            "- If a response is ambiguous, set participant status to UNKNOWN.",
            "- Use APPROVED only for clear approval/confirmation language.",
            "- Use REJECTED only for clear rejection language.",
            "- If a person clearly says ok/okay/approved/yes/confirmed/done, set status to OK or APPROVED depending on wording and context.",
            "- If they say will check / checking / will confirm / not yet / let me verify, use PENDING.",
            "- If the description is unclear, set description to null.",
            "- Use uppercase values only.",
        ]
    )

    return "\n".join(prompt_lines)


def _extract_response_payload(response: Any) -> dict[str, Any]:
    if response is None:
        raise ValueError("Gemini response was empty.")

    if hasattr(response, "parsed") and response.parsed is not None:
        parsed = response.parsed
        if isinstance(parsed, dict):
            return parsed
        if hasattr(parsed, "model_dump"):
            return parsed.model_dump()

    text = getattr(response, "text", None)
    if text:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            raise ValueError("Gemini returned non-JSON content.")

    if hasattr(response, "candidates") and response.candidates:
        candidate = response.candidates[0]
        if hasattr(candidate, "content"):
            parts = getattr(candidate.content, "parts", [])
            if parts:
                part_text = getattr(parts[0], "text", None)
                if part_text:
                    return json.loads(part_text)

    raise ValueError("Could not interpret Gemini response payload.")


def call_gemini(case: dict[str, Any], client: Any | None = None, model_name: str | None = None) -> dict[str, Any]:
    """Call Gemini for the covering case using structured JSON output."""
    if client is not None and not hasattr(client, "models"):
        return _extract_response_payload(client)

    if client is None:
        try:
            from google import genai
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("google-genai is required for Gemini integration.") from exc

        api_key = _require_api_key()
        client = genai.Client(api_key=api_key)

    model = model_name or get_gemini_model_name()
    prompt = _build_prompt(case)

    config = {
        "response_mime_type": "application/json",
        "response_schema": CaseInterpretation.model_json_schema(),
    }

    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=config,
    )
    return _extract_response_payload(response)


def interpret_covering_case(case: dict[str, Any], gemini_client: Any | None = None) -> CaseInterpretation:
    """Interpret a single covering case into a validated, structured result."""
    raw_response = call_gemini(case, client=gemini_client)
    return CaseInterpretation.model_validate(raw_response)


def interpret_cases(cases: list[dict[str, Any]], gemini_client: Any | None = None) -> list[CaseInterpretation]:
    return [interpret_covering_case(case, gemini_client=gemini_client) for case in cases]


def process_whatsapp_text(raw_text: str, gemini_client: Any | None = None) -> list[CaseInterpretation]:
    messages = parse_whatsapp_export(raw_text)
    cases = extract_covering_cases(messages)
    return interpret_cases(cases, gemini_client=gemini_client)
