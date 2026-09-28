import os
import re
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv
from supabase import create_client

from ai_processor import CaseInterpretation, OverallStatus, ParticipantStatus

REQUIRED_APPROVALS: tuple[dict[str, Any], ...] = (
    {"role": "PROJECT MANAGER", "names": {"mukesh"}},
    {"role": "ADD GENERAL MANAGER", "names": {"mehta", "mehta ji spm dv bsp site", "mehta sir"}},
    {"role": "GENERAL MANAGER", "names": {"jagga", "jagga rao", "jagga rao sir", "jagga sir"}},
    {"role": "CLUSTER HEAD", "names": {"gagan", "gagan deep singh", "gagan deep singh sir", "gagan sir"}},
    {"role": "DIRECTOR", "names": {"deep", "deep patel", "deep patel sir", "deep sir"}},
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _unwrap_rows(result: Any) -> list[dict[str, Any]]:
    if result is None:
        return []
    if hasattr(result, "data"):
        data = result.data
    else:
        data = result
    if data is None:
        return []
    if isinstance(data, list):
        return data
    return [data]


def load_environment() -> dict[str, str]:
    load_dotenv()

    required = ["SUPABASE_URL", "SUPABASE_SECRET_KEY"]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

    return {
        "SUPABASE_URL": os.getenv("SUPABASE_URL"),
        "SUPABASE_SECRET_KEY": os.getenv("SUPABASE_SECRET_KEY"),
    }


def get_supabase_client():
    env = load_environment()
    return create_client(env["SUPABASE_URL"], env["SUPABASE_SECRET_KEY"])


def _coerce_case_interpretation(value: Any) -> CaseInterpretation:
    if isinstance(value, CaseInterpretation):
        return value
    if isinstance(value, dict):
        return CaseInterpretation.model_validate(value)
    raise TypeError("Expected a CaseInterpretation instance or a dict-like payload.")


def _normalize_participant(participant: dict[str, Any]) -> dict[str, Any]:
    person = str(participant.get("person", "")).strip()
    if not person:
        raise ValueError("Participant person is required.")

    raw_status = participant.get("status")
    if isinstance(raw_status, ParticipantStatus):
        status = raw_status.value
    else:
        status = str(raw_status or "").upper()
    if status not in {item.value for item in ParticipantStatus}:
        raise ValueError(f"Invalid participant status: {status}")

    last_message = participant.get("last_message")
    return {
        "person": person,
        "status": status,
        "last_message": last_message.strip() if isinstance(last_message, str) else last_message,
    }


def _normalize_message(raw_message: dict[str, Any]) -> dict[str, Any]:
    sender = str(raw_message.get("sender", "")).strip()
    message_text = str(raw_message.get("text", raw_message.get("message", ""))).strip()
    if not sender or not message_text:
        raise ValueError("Each message must include sender and message text.")

    return {
        "sender": sender,
        "message": message_text,
        "timestamp": raw_message.get("timestamp"),
    }


def _merge_optional_value(existing: Any, incoming: Any) -> Any:
    if incoming is None or incoming == "UNKNOWN":
        return existing
    return incoming


def _normalize_person_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _person_matches_any_alias(person_key: str, aliases: set[str]) -> bool:
    if not person_key:
        return False
    for alias in aliases:
        normalized_alias = _normalize_person_key(alias)
        if not normalized_alias:
            continue
        if person_key == normalized_alias or person_key.startswith(normalized_alias) or normalized_alias.startswith(person_key):
            return True
        if normalized_alias in person_key or person_key in normalized_alias:
            return True
    return False


def _determine_required_approval_status(participants: list[dict[str, Any]]) -> OverallStatus:
    approved_roles: set[str] = set()

    for participant in participants:
        if not isinstance(participant, dict):
            continue

        person = str(participant.get("person") or "").strip()
        status = str(participant.get("status") or "").upper()
        if not person or status not in {ParticipantStatus.APPROVED.value, ParticipantStatus.OK.value}:
            continue

        normalized_person = _normalize_person_key(person)
        for requirement in REQUIRED_APPROVALS:
            if _person_matches_any_alias(normalized_person, requirement["names"]):
                approved_roles.add(requirement["role"])
                break

    return OverallStatus.APPROVED if len(approved_roles) >= len(REQUIRED_APPROVALS) else OverallStatus.PENDING


def _resolve_case_payload(case_data: dict[str, Any], interpretation: Any | None = None) -> dict[str, Any]:
    if interpretation is not None:
        parsed = _coerce_case_interpretation(interpretation)
        base = parsed.model_dump()
    else:
        base = {}

    if isinstance(case_data, dict):
        base.update(case_data)

    covering_number = str(base.get("covering_number", "")).strip()
    if not covering_number:
        raise ValueError("covering_number is required.")

    description = base.get("description")
    urgency = base.get("urgency")
    explicit_overall_status = base.get("overall_status")
    if explicit_overall_status is not None:
        normalized_overall_status = (
            explicit_overall_status.value
            if isinstance(explicit_overall_status, OverallStatus)
            else str(explicit_overall_status).upper()
        )
        if normalized_overall_status not in {item.value for item in OverallStatus}:
            raise ValueError(f"Invalid overall status: {normalized_overall_status}")

    participants = []
    for item in base.get("participants", []):
        participants.append(_normalize_participant(item))

    messages = []
    for item in base.get("messages", []):
        messages.append(_normalize_message(item))

    if not participants and "participants" in case_data:
        for item in case_data.get("participants", []):
            if isinstance(item, str):
                participants.append({"person": item, "status": ParticipantStatus.UNKNOWN.value, "last_message": None})

    overall_status = _determine_required_approval_status(participants).value

    case_payload = {
        "covering_number": covering_number,
        "description": description,
        "urgency": urgency.upper() if isinstance(urgency, str) and urgency else None,
        "raised_by": base.get("raised_by") or (participants[0]["person"] if participants else None),
        "overall_status": overall_status,
        "first_seen": base.get("first_seen"),
        "last_seen": base.get("last_seen"),
    }

    return {
        "case": case_payload,
        "participants": participants,
        "messages": messages,
    }


def get_or_create_case(client: Any, covering_number: str) -> dict[str, Any]:
    rows = client.table("cases").select("*").eq("covering_number", covering_number).execute()
    data = _unwrap_rows(rows)
    if data:
        return data[0]

    record = {
        "covering_number": covering_number,
        "description": None,
        "urgency": None,
        "raised_by": None,
        "overall_status": OverallStatus.UNKNOWN.value,
        "first_seen": None,
        "last_seen": None,
        "created_at": _utc_now(),
        "updated_at": _utc_now(),
    }
    result = client.table("cases").insert(record).execute()
    payload = _unwrap_rows(result)
    return payload[0]


def upsert_case(client: Any, case_data: dict[str, Any], interpretation: Any | None = None) -> dict[str, Any]:
    resolved = _resolve_case_payload(case_data, interpretation)
    incoming_case = resolved["case"]

    existing = get_or_create_case(client, incoming_case["covering_number"])
    merged = {
        "id": existing.get("id"),
        "covering_number": incoming_case["covering_number"],
        "description": _merge_optional_value(existing.get("description"), incoming_case.get("description")),
        "urgency": _merge_optional_value(existing.get("urgency"), incoming_case.get("urgency")),
        "raised_by": _merge_optional_value(existing.get("raised_by"), incoming_case.get("raised_by")),
        "overall_status": incoming_case["overall_status"],
        "first_seen": incoming_case.get("first_seen") or existing.get("first_seen"),
        "last_seen": incoming_case.get("last_seen") or existing.get("last_seen"),
        "created_at": existing.get("created_at"),
        "updated_at": _utc_now(),
    }

    result = client.table("cases").upsert(merged, on_conflict="covering_number").execute()
    payload = _unwrap_rows(result)
    return payload[0] if payload else merged


def upsert_participant(client: Any, case_id: str, participant: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_participant(participant)
    payload = {
        "case_id": case_id,
        "person": normalized["person"],
        "status": normalized["status"],
        "last_message": normalized.get("last_message"),
        "updated_at": _utc_now(),
    }
    result = client.table("participants").upsert(payload, on_conflict="case_id,person").execute()
    data = _unwrap_rows(result)
    return data[0] if data else payload


def insert_message(client: Any, case_id: str, message: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_message(message)
    sender = normalized["sender"]
    message_text = normalized["message"]
    timestamp = normalized["timestamp"]

    query = client.table("messages").select("*").eq("case_id", case_id).eq("sender", sender).eq("message", message_text)
    if timestamp is not None:
        query = query.eq("timestamp", timestamp)
    existing = query.execute()
    data = _unwrap_rows(existing)
    if data:
        return data[0]

    payload = {
        "case_id": case_id,
        "sender": sender,
        "message": message_text,
        "timestamp": timestamp,
        "created_at": _utc_now(),
    }
    result = client.table("messages").insert(payload).execute()
    payload_data = _unwrap_rows(result)
    return payload_data[0] if payload_data else payload


def get_cases(client: Any | None = None, status: str | None = None, urgency: str | None = None) -> list[dict[str, Any]]:
    db_client = client or get_supabase_client()
    query = db_client.table("cases").select("*")
    if status:
        query = query.eq("overall_status", str(status).upper())
    if urgency:
        query = query.eq("urgency", str(urgency).upper())
    query = query.order("updated_at", desc=True)
    rows = query.execute()
    return _unwrap_rows(rows)


def get_case_by_covering_number(client: Any | None = None, covering_number: str | None = None) -> dict[str, Any] | None:
    if not covering_number:
        return None
    db_client = client or get_supabase_client()
    rows = db_client.table("cases").select("*").eq("covering_number", str(covering_number)).execute()
    data = _unwrap_rows(rows)
    return data[0] if data else None


def get_participants(client: Any | None = None, case_id: str | None = None) -> list[dict[str, Any]]:
    if not case_id:
        return []
    db_client = client or get_supabase_client()
    rows = db_client.table("participants").select("*").eq("case_id", case_id).order("person").execute()
    return _unwrap_rows(rows)


def get_messages(client: Any | None = None, case_id: str | None = None) -> list[dict[str, Any]]:
    if not case_id:
        return []
    db_client = client or get_supabase_client()
    rows = db_client.table("messages").select("*").eq("case_id", case_id).order("timestamp").execute()
    return _unwrap_rows(rows)


def save_case(client: Any, case_data: dict[str, Any], interpretation: Any | None = None) -> dict[str, Any]:
    resolved = _resolve_case_payload(case_data, interpretation)
    case_payload = resolved["case"]
    case_payload["participants"] = resolved["participants"]
    case_record = upsert_case(client, case_payload, interpretation)

    for participant in resolved["participants"]:
        upsert_participant(client, case_record["id"], participant)

    for message in resolved["messages"]:
        insert_message(client, case_record["id"], message)

    return case_record
