import json
import os
import re
from enum import Enum
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field, field_validator

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


_APPROVER_ALIASES = (
    ("Mehta Ji SPM Dv Bsp Site", "Mehta Ji SPM Dv Bsp Site"),
    ("Gagan Deep Singh", "Gagan Deep Singh"),
    ("Jagga Rao Sir", "Jagga Rao"),
    ("Deep Patel Sir", "Deep Patel"),
    ("Mehta Ji", "Mehta Ji SPM Dv Bsp Site"),
    ("Mehta Sir", "Mehta Ji SPM Dv Bsp Site"),
    ("Jagga Sir", "Jagga Rao"),
    ("Jagga Rao", "Jagga Rao"),
    ("Gagan Sir", "Gagan Deep Singh"),
    ("Deep Sir", "Deep Patel"),
    ("Deep Patel", "Deep Patel"),
    ("Mukesh Sir", "Mukesh"),
    ("Mukesh", "Mukesh"),
)


def _canonical_approver_name(value: str) -> str | None:
    normalized = re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()
    for alias, canonical_name in _APPROVER_ALIASES:
        alias_normalized = re.sub(r"[^a-z0-9]+", " ", alias.casefold()).strip()
        if normalized == alias_normalized:
            return canonical_name
    return None


class ParticipantInterpretation(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "additionalProperties": False,
            "required": ["person", "status", "last_message"],
        },
    )

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
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "additionalProperties": False,
            "required": ["covering_number", "description", "urgency", "participants", "overall_status"],
        },
    )

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
    return os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b")


def _require_api_key() -> str:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise ValueError("GROQ_API_KEY is not set. Add it to the environment or .env file.")
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
            "- If a message says 'approved by [name]' or '[name] approved', attribute the approval to the named person, not the message sender.",
            "- Normalize named approvers: Mukesh/Mukesh Sir to Mukesh; Mehta Sir/Mehta Ji to Mehta Ji SPM Dv Bsp Site; Jagga Sir/Jagga Rao Sir to Jagga Rao; Gagan Sir/Gagan Deep Singh to Gagan Deep Singh; Deep Sir/Deep Patel Sir to Deep Patel.",
            "- If they say will check / checking / will confirm / not yet / let me verify, use PENDING.",
            "- If the description is unclear, set description to null.",
            "- Use uppercase values only.",
        ]
    )

    return "\n".join(prompt_lines)


def _extract_response_payload(response: Any) -> dict[str, Any]:
    if response is None:
        raise ValueError("AI response was empty.")

    if hasattr(response, "parsed") and response.parsed is not None:
        parsed = response.parsed
        if isinstance(parsed, dict):
            return parsed
        if hasattr(parsed, "model_dump"):
            return parsed.model_dump()

    choices = getattr(response, "choices", None)
    if choices:
        choice = choices[0]
        message = getattr(choice, "message", None)
        text = getattr(message, "content", None)
        if isinstance(text, str) and text.strip():
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                raise ValueError("Groq returned non-JSON content.")

    text = getattr(response, "text", None)
    if isinstance(text, str) and text.strip():
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            raise ValueError("Groq returned non-JSON content.")

    if hasattr(response, "output_text") and response.output_text:
        try:
            return json.loads(response.output_text)
        except json.JSONDecodeError:
            raise ValueError("Groq returned non-JSON content.")

    raise ValueError("Could not interpret Groq response payload.")


def call_gemini(case: dict[str, Any], client: Any | None = None, model_name: str | None = None) -> dict[str, Any]:
    """Call Groq's OpenAI-compatible API for the covering case using structured JSON output."""
    if client is not None and not hasattr(client, "chat"):
        return _extract_response_payload(client)

    if client is None:
        api_key = _require_api_key()
        client = OpenAI(
            api_key=api_key,
            base_url="https://api.groq.com/openai/v1",
        )

    model = model_name or get_gemini_model_name()
    prompt = _build_prompt(case)

    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=2048,
        temperature=0,
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "case_interpretation",
                "schema": CaseInterpretation.model_json_schema(),
                "strict": True,
            },
        },
    )
    return _extract_response_payload(response)


def interpret_covering_case(case: dict[str, Any], gemini_client: Any | None = None) -> CaseInterpretation:
    """Interpret a single covering case into a validated, structured result."""
    raw_response = call_gemini(case, client=gemini_client)
    interpretation = CaseInterpretation.model_validate(raw_response)
    explicit_approvals: dict[str, tuple[str, str]] = {}

    for message in case.get("messages", []):
        text = str(message.get("text") or "")
        for alias, canonical_name in _APPROVER_ALIASES:
            alias_pattern = r"\s+".join(re.escape(part) for part in alias.split())
            if re.search(rf"\bapproved\s+by\s+{alias_pattern}\b", text, re.IGNORECASE) or re.search(
                rf"\b{alias_pattern}\s+(?:has\s+)?approved\b", text, re.IGNORECASE
            ):
                explicit_approvals[canonical_name] = (str(message.get("sender") or ""), text)
                break

    if not explicit_approvals:
        return interpretation

    participants = [
        participant
        for participant in interpretation.participants
        if not any(
            participant.person.strip().casefold() == sender.strip().casefold()
            and participant.status in {ParticipantStatus.APPROVED, ParticipantStatus.OK}
            and participant.last_message.strip().casefold() == text.strip().casefold()
            for sender, text in explicit_approvals.values()
        )
    ]

    for canonical_name, (_, text) in explicit_approvals.items():
        matching_participant = next(
            (
                participant
                for participant in participants
                if _canonical_approver_name(participant.person) == canonical_name
            ),
            None,
        )
        if matching_participant:
            matching_participant.person = canonical_name
            matching_participant.status = ParticipantStatus.APPROVED
            matching_participant.last_message = text
            participants = [
                participant
                for participant in participants
                if participant is matching_participant
                or _canonical_approver_name(participant.person) != canonical_name
            ]
        else:
            participants.append(
                ParticipantInterpretation(
                    person=canonical_name,
                    status=ParticipantStatus.APPROVED,
                    last_message=text,
                )
            )

    return interpretation.model_copy(update={"participants": participants})


def interpret_cases(cases: list[dict[str, Any]], gemini_client: Any | None = None) -> list[CaseInterpretation]:
    return [interpret_covering_case(case, gemini_client=gemini_client) for case in cases]


def process_whatsapp_text(raw_text: str, gemini_client: Any | None = None) -> list[CaseInterpretation]:
    messages = parse_whatsapp_export(raw_text)
    cases = extract_covering_cases(messages)
    return interpret_cases(cases, gemini_client=gemini_client)
