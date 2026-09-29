from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build

from database import get_cases, get_messages, get_participants, get_supabase_client

load_dotenv()

CASE_SHEET_NAME = "Cases"
PARTICIPANTS_SHEET_NAME = "Participants"
MESSAGES_SHEET_NAME = "Messages"
CASE_HISTORY_SHEET_NAME = "Case History"
LEGACY_SHEET_NAMES = ["Sheet1", "Participants", "Messages"]
_legacy_cleanup_checked = False

REQUIRED_APPROVAL_ROLES = [
    "PROJECT MANAGER",
    "ADD GENERAL MANAGER",
    "GENERAL MANAGER",
    "CLUSTER HEAD",
    "DIRECTOR",
]

ROLE_CONTACT_MAPPINGS: dict[str, dict[str, Any]] = {
    "PROJECT MANAGER": {
        "contacts": ["Mukesh"],
        "display_name": "Mukesh Sir",
    },
    "ADD GENERAL MANAGER": {
        "contacts": ["Mehta Ji SPM Dv Bsp Site", "Mehta Sir"],
        "display_name": "Mehta Sir",
    },
    "GENERAL MANAGER": {
        "contacts": ["Jagga Rao Sir", "Jagga Sir"],
        "display_name": "Jagga Rao Sir",
    },
    "CLUSTER HEAD": {
        "contacts": ["Gagan Deep Singh", "Gagan Sir"],
        "display_name": "Gagan Sir",
    },
    "DIRECTOR": {
        "contacts": ["Deep Patel Sir", "Deep Sir"],
        "display_name": "Deep Patel Sir",
    },
}


def _normalize_key(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip()


def normalize_whatsapp_name(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""

    norm = _normalize_key(text)
    if not norm:
        return text
    if norm == "mukesh":
        return "mukesh"
    if norm.startswith("mehta"):
        return "mehta"
    if norm.startswith("jagga"):
        return "jagga"
    if norm.startswith("gagan"):
        return "gagan deep singh" if "deep" in norm else "gagan"
    if norm.startswith("deep"):
        return "deep patel sir" if "patel" in norm else "deep"
    if norm.startswith("utpal"):
        return "utpal singh"
    return norm


def resolve_role_display_name(contact_name: Any, role_name: str) -> str:
    text = str(contact_name or "").strip()
    if not text:
        return ""

    mapping = ROLE_CONTACT_MAPPINGS.get(role_name.upper())
    if mapping:
        contacts = [str(item).strip() for item in mapping["contacts"]]
        for contact in contacts:
            if _normalize_key(contact) == _normalize_key(text):
                return mapping["display_name"]
            if _normalize_key(text).startswith(_normalize_key(contact).split()[0]):
                return mapping["display_name"]

    if text.lower() in {"mukesh", "mehta sir", "gagan sir", "jagga sir", "deep sir"}:
        return text

    return text


def determine_approval_status(approved_count: int) -> str:
    if approved_count >= 5:
        return "🟢"
    if approved_count > 0:
        return "🟡"
    return "🔴"


def _match_person_to_role(person_name: Any) -> str | None:
    person = str(person_name or "").strip()
    if not person:
        return None
    for role, mapping in ROLE_CONTACT_MAPPINGS.items():
        for contact in mapping["contacts"]:
            if _normalize_key(contact) == _normalize_key(person):
                return role
            if _normalize_key(person).startswith(_normalize_key(contact).split()[0]):
                return role
    return None


def _display_person_name(person_name: Any) -> str:
    person = str(person_name or "").strip()
    if not person:
        return ""

    role = _match_person_to_role(person)
    if role:
        return ROLE_CONTACT_MAPPINGS[role]["display_name"]
    return person


def _is_approval_message(text: Any) -> bool:
    candidate = str(text or "").lower()
    if not candidate:
        return False
    approval_keywords = [
        "approved",
        "approve",
        "ok",
        "okay",
        "confirmed",
        "done",
        "clear",
        "complete",
        "finalized",
    ]
    return any(keyword in candidate for keyword in approval_keywords)


def _get_case_first_seen(case: dict[str, Any]) -> str:
    value = case.get("first_seen") or case.get("created_at") or case.get("updated_at") or ""
    return str(value)


def _as_ist(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(ZoneInfo("Asia/Kolkata"))


def _format_date(value: str | None) -> str:
    formatted = _as_ist(value)
    if formatted:
        return formatted.strftime("%d-%m-%y")
    return str(value).split("T", 1)[0] if value and "T" in str(value) else str(value or "")


def _format_time(value: str | None) -> str:
    formatted = _as_ist(value)
    if formatted:
        return formatted.strftime("%H:%M")
    return str(value).split("T", 1)[1][:5] if value and "T" in str(value) else str(value or "")


def _format_datetime(value: str | None) -> str:
    formatted = _as_ist(value)
    if formatted:
        return formatted.strftime("%d-%m-%y %H:%M")
    return str(value or "")


def _get_case_messages(case: dict[str, Any], messages: list[dict[str, Any]] | None = None, client: Any | None = None) -> list[dict[str, Any]]:
    if messages is not None:
        return messages
    case_id = case.get("id")
    if not case_id:
        return []
    return get_messages(client=client, case_id=case_id)


def _find_fifth_approval_timestamp(messages: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    approved_roles: set[str] = set()
    for message in messages:
        sender = str(message.get("sender") or message.get("person") or "").strip()
        text = message.get("text") or message.get("message") or ""
        if not sender or not _is_approval_message(text):
            continue
        role = _match_person_to_role(sender) or _match_named_approval_role(text)
        if role and role not in approved_roles:
            approved_roles.add(role)
            if len(approved_roles) == 5:
                return str(message.get("timestamp") or ""), role
    return None, None


def _match_named_approval_role(text: Any) -> str | None:
    candidate = str(text or "")
    for role, mapping in ROLE_CONTACT_MAPPINGS.items():
        names = [*mapping["contacts"], mapping["display_name"]]
        for name in sorted(names, key=len, reverse=True):
            alias_pattern = r"\s+".join(re.escape(part) for part in name.split())
            if re.search(rf"\bapproved\s+by\s+{alias_pattern}\b", candidate, re.IGNORECASE):
                return role
            if re.search(rf"\b{alias_pattern}\s+(?:has\s+)?approved\b", candidate, re.IGNORECASE):
                return role
    return None


def _get_case_approval_status(case: dict[str, Any], participants: list[dict[str, Any]] | None = None) -> tuple[dict[str, Any], int]:
    approvals: dict[str, str] = {}
    participant_records = participants or []
    for participant in participant_records:
        person = str(participant.get("person") or "").strip()
        if not person:
            continue
        status = str(participant.get("status") or "").upper()
        if status in {"APPROVED", "OK"}:
            role = _match_person_to_role(person)
            if role:
                approvals[role] = person

    approved_count = len(approvals)
    if approved_count > 0:
        pass
    return approvals, approved_count


def build_case_rows(cases: list[dict[str, Any]], participants: list[dict[str, Any]] | None = None, messages: list[dict[str, Any]] | None = None) -> list[list[Any]]:
    if participants is None and messages is None:
        header = [
            "Covering Number",
            "Description",
            "Urgency",
            "Raised By",
            "Overall Status",
            "First Seen",
            "Last Updated",
        ]
        rows = [header]
        for case in cases:
            rows.append(
                [
                    str(case.get("covering_number") or ""),
                    case.get("description") or "",
                    str(case.get("urgency") or "").upper(),
                    case.get("raised_by") or "",
                    str(case.get("overall_status") or "").upper(),
                    case.get("first_seen") or "",
                    case.get("updated_at") or case.get("last_seen") or "",
                ]
            )
        return rows

    summary_header = [
        "Case No.",
        "Description",
        "Project Manager",
        "Add General Manager",
        "GENERAL MANAGER",
        "CLUSTER HEAD",
        "DIRECTOR",
        "Approval Status",
        "First Generated",
    ]
    rows = [summary_header]

    for case in cases:
        covering_number = str(case.get("covering_number") or "")
        description = case.get("description") or ""
        first_seen = _get_case_first_seen(case)

        case_participants = [
            item for item in (participants or [])
            if str(item.get("covering_number") or item.get("case_id") or "") == str(covering_number)
            or str(item.get("case_id") or "") == str(case.get("id") or "")
        ]
        approvals, approved_count = _get_case_approval_status(case, case_participants)
        role_values = {}
        for role in REQUIRED_APPROVAL_ROLES:
            role_person = None
            for participant in case_participants:
                person = str(participant.get("person") or "").strip()
                if _match_person_to_role(person) == role:
                    status = str(participant.get("status") or "").upper()
                    if status in {"APPROVED", "OK"}:
                        role_person = person
                        break
            role_values[role] = f"🟩 {resolve_role_display_name(role_person, role)}" if role_person else ""

        rows.append(
            [
                covering_number,
                description,
                role_values["PROJECT MANAGER"],
                role_values["ADD GENERAL MANAGER"],
                role_values["GENERAL MANAGER"],
                role_values["CLUSTER HEAD"],
                role_values["DIRECTOR"],
                determine_approval_status(approved_count),
                _format_datetime(first_seen),
            ]
        )

    return rows


def build_participant_rows(participants: list[dict[str, Any]]) -> list[list[Any]]:
    header = ["Covering Number", "Person", "Status", "Last Message", "Updated"]
    rows = [header]
    for participant in participants:
        rows.append(
            [
                str(participant.get("covering_number") or ""),
                participant.get("person") or "",
                str(participant.get("status") or "").upper(),
                participant.get("last_message") or "",
                participant.get("updated_at") or "",
            ]
        )
    return rows


def build_message_rows(messages: list[dict[str, Any]]) -> list[list[Any]]:
    header = ["Covering Number", "Timestamp", "Sender", "Message"]
    rows = [header]
    for message in messages:
        rows.append(
            [
                str(message.get("covering_number") or ""),
                message.get("timestamp") or "",
                message.get("sender") or "",
                message.get("message") or message.get("text") or "",
            ]
        )
    return rows


def build_case_history_rows(case: dict[str, Any], messages: list[dict[str, Any]]) -> list[list[Any]]:
    rows: list[list[Any]] = []
    case_number = str(case.get("covering_number") or "")
    first_seen = str(case.get("first_seen") or "")
    generated_by = case.get("raised_by") or next(
        (message.get("sender") for message in messages if message.get("sender")),
        "Unknown",
    )

    rows.append([f"CASE NO: {case_number}"])
    rows.append(["First Generated:"])
    rows.append([f"Date: {_format_date(first_seen)}"])
    rows.append([f"Time: {_format_time(first_seen)}"])
    rows.append([f"Generated by: {generated_by}"])
    rows.append(["Finalized:"])

    final_timestamp, _ = _find_fifth_approval_timestamp(messages)
    if final_timestamp:
        rows.append([f"Date: {_format_date(final_timestamp)}"])
        rows.append([f"Time: {_format_time(final_timestamp)}"])
        rows.append(["Status: APPROVED"])
    else:
        rows.append(["Status: PENDING"])

    rows.append([])
    rows.append(["timestamp", "sender", "message"])
    for message in messages:
        sender = str(message.get("sender") or "").strip()
        text = str(message.get("text") or message.get("message") or "")
        rows.append([message.get("timestamp") or "", _display_person_name(sender), text])

    return rows


def _get_sheet_id(service: Any, spreadsheet_id: str, sheet_title: str) -> int | None:
    response = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    for sheet in response.get("sheets", []):
        props = sheet.get("properties", {})
        if props.get("title") == sheet_title:
            return props.get("sheetId")
    return None


def _ensure_sheet(service: Any, spreadsheet_id: str, sheet_title: str) -> bool:
    response = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    existing_titles = {
        sheet["properties"]["title"] for sheet in response.get("sheets", []) if "properties" in sheet and "title" in sheet["properties"]
    }
    if sheet_title in existing_titles:
        return False

    body = {"requests": [{"addSheet": {"properties": {"title": sheet_title}}}]}
    service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body=body).execute()
    return True


def _write_rows_to_sheet(service: Any, spreadsheet_id: str, sheet_title: str, rows: list[list[Any]]) -> bool:
    created = _ensure_sheet(service, spreadsheet_id, sheet_title)
    width = max((len(row) for row in rows), default=1)
    normalized_rows = [row + [""] * (width - len(row)) for row in rows]
    existing = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_title}!A:Z",
    ).execute()
    old_row_count = len(existing.get("values", []))
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_title}!A1",
        valueInputOption="RAW",
        body={"values": normalized_rows},
    ).execute()
    if old_row_count > len(rows):
        service.spreadsheets().values().clear(
            spreadsheetId=spreadsheet_id,
            range=f"{sheet_title}!A{len(rows) + 1}:Z{old_row_count}",
            body={},
        ).execute()
    return created


def _apply_approval_status_formatting(service: Any, spreadsheet_id: str, sheet_title: str) -> None:
    if sheet_title != CASE_SHEET_NAME:
        return
    sheet_id = _get_sheet_id(service, spreadsheet_id, sheet_title)
    if sheet_id is None:
        return

    requests = [
        {
            "repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": 9},
                "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                "fields": "userEnteredFormat(textFormat)",
            }
        },
        {
            "repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": 2000, "startColumnIndex": 7, "endColumnIndex": 8},
                "cell": {
                    "userEnteredFormat": {
                        "horizontalAlignment": "CENTER",
                        "backgroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0},
                    }
                },
                "fields": "userEnteredFormat(horizontalAlignment,backgroundColor)",
            }
        },
    ]
    service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": requests}).execute()


def _set_case_column_widths(service: Any, spreadsheet_id: str, sheet_title: str) -> None:
    if sheet_title != CASE_SHEET_NAME:
        return
    sheet_id = _get_sheet_id(service, spreadsheet_id, sheet_title)
    if sheet_id is None:
        return
    requests = [
        {"updateDimensionProperties": {"range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 1}, "properties": {"pixelSize": 140}, "fields": "pixelSize"}},
        {"updateDimensionProperties": {"range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 1, "endIndex": 2}, "properties": {"pixelSize": 360}, "fields": "pixelSize"}},
        {"updateDimensionProperties": {"range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 2, "endIndex": 3}, "properties": {"pixelSize": 130}, "fields": "pixelSize"}},
        {"updateDimensionProperties": {"range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 3, "endIndex": 4}, "properties": {"pixelSize": 130}, "fields": "pixelSize"}},
        {"updateDimensionProperties": {"range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 4, "endIndex": 5}, "properties": {"pixelSize": 130}, "fields": "pixelSize"}},
        {"updateDimensionProperties": {"range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 5, "endIndex": 6}, "properties": {"pixelSize": 130}, "fields": "pixelSize"}},
        {"updateDimensionProperties": {"range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 6, "endIndex": 7}, "properties": {"pixelSize": 130}, "fields": "pixelSize"}},
        {"updateDimensionProperties": {"range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 7, "endIndex": 8}, "properties": {"pixelSize": 120}, "fields": "pixelSize"}},
        {"updateDimensionProperties": {"range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 8, "endIndex": 9}, "properties": {"pixelSize": 160}, "fields": "pixelSize"}},
    ]
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": requests},
    ).execute()


def _delete_legacy_sheets(service: Any, spreadsheet_id: str) -> None:
    response = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    requests = []
    for sheet in response.get("sheets", []):
        props = sheet.get("properties", {})
        title = props.get("title")
        if title in LEGACY_SHEET_NAMES:
            requests.append({"deleteSheet": {"sheetId": props["sheetId"]}})
    if requests:
        service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": requests}).execute()


def _write_case_history_sheet(
    service: Any,
    spreadsheet_id: str,
    cases: list[dict[str, Any]],
    messages_by_case: dict[str, list[dict[str, Any]]],
) -> None:
    rows: list[list[Any]] = []
    for case in cases:
        case_messages = messages_by_case.get(str(case.get("id") or ""), [])
        rows.extend(build_case_history_rows(case, case_messages))
        rows.append([])
    if rows:
        _write_rows_to_sheet(service, spreadsheet_id, CASE_HISTORY_SHEET_NAME, rows)


def sync_cases_to_sheet(service: Any, spreadsheet_id: str, cases: list[dict[str, Any]], participants: list[dict[str, Any]] | None = None, messages: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = build_case_rows(cases, participants, messages)
    _write_rows_to_sheet(service, spreadsheet_id, CASE_SHEET_NAME, rows)
    return {"sheet": CASE_SHEET_NAME, "row_count": len(rows) - 1}


def sync_participants_to_sheet(service: Any, spreadsheet_id: str, participants: list[dict[str, Any]]) -> dict[str, Any]:
    rows = build_participant_rows(participants)
    _write_rows_to_sheet(service, spreadsheet_id, PARTICIPANTS_SHEET_NAME, rows)
    return {"sheet": PARTICIPANTS_SHEET_NAME, "row_count": len(rows) - 1}


def sync_messages_to_sheet(service: Any, spreadsheet_id: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
    rows = build_message_rows(messages)
    _write_rows_to_sheet(service, spreadsheet_id, MESSAGES_SHEET_NAME, rows)
    return {"sheet": MESSAGES_SHEET_NAME, "row_count": len(rows) - 1}


def sync_all_to_google_sheets(service: Any | None = None, spreadsheet_id: str | None = None) -> dict[str, Any]:
    spreadsheet_id = spreadsheet_id or _get_spreadsheet_id()
    if service is None:
        service = get_sheets_service()

    client = get_supabase_client()
    cases = get_cases(client=client)
    covering_numbers = {str(case.get("id")): str(case.get("covering_number") or "") for case in cases}
    participants_rows = []
    for participant in get_participants(client=client, all_cases=True):
        enriched = dict(participant)
        enriched["covering_number"] = covering_numbers.get(str(participant.get("case_id") or ""), "")
        participants_rows.append(enriched)

    messages_by_case: dict[str, list[dict[str, Any]]] = {}
    messages_rows = []
    for message in get_messages(client=client, all_cases=True):
        enriched = dict(message)
        case_id = str(message.get("case_id") or "")
        enriched["covering_number"] = covering_numbers.get(case_id, "")
        messages_rows.append(enriched)
        messages_by_case.setdefault(case_id, []).append(enriched)

    sync_cases_to_sheet(service, spreadsheet_id, cases, participants_rows, messages_rows)
    _write_case_history_sheet(service, spreadsheet_id, cases, messages_by_case)

    global _legacy_cleanup_checked
    if not _legacy_cleanup_checked:
        _apply_approval_status_formatting(service, spreadsheet_id, CASE_SHEET_NAME)
        _set_case_column_widths(service, spreadsheet_id, CASE_SHEET_NAME)
        _delete_legacy_sheets(service, spreadsheet_id)
        _legacy_cleanup_checked = True

    response = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    titles = [sheet["properties"]["title"] for sheet in response.get("sheets", []) if "properties" in sheet and "title" in sheet["properties"]]
    if "Cases" not in titles:
        raise ValueError("Cases sheet was not created successfully.")
    if "Case History" not in titles:
        raise ValueError("Case History sheet was not created successfully.")
    if any(title in {"Sheet1", "Participants", "Messages"} for title in titles):
        raise ValueError("Legacy sheets were not cleaned up successfully.")

    return {
        "spreadsheet_id": spreadsheet_id,
        "cases": len(cases),
        "participants": len(participants_rows),
        "messages": len(messages_rows),
    }


def _get_spreadsheet_id() -> str:
    spreadsheet_id = os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID")
    if not spreadsheet_id:
        raise ValueError("Missing required environment variable: GOOGLE_SHEETS_SPREADSHEET_ID")
    return spreadsheet_id


def _get_credentials_path() -> str:
    credentials_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_CREDENTIALS_PATH") or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if not credentials_path:
        raise ValueError(
            "Missing Google credentials configuration. Set GOOGLE_SERVICE_ACCOUNT_CREDENTIALS_PATH or GOOGLE_APPLICATION_CREDENTIALS to the service account JSON file."
        )
    return credentials_path


def get_sheets_service() -> Any:
    credentials_path = _get_credentials_path()
    if not os.path.exists(credentials_path):
        raise ValueError(f"Google credentials file not found: {credentials_path}")
    credentials = service_account.Credentials.from_service_account_file(
        credentials_path,
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    return build("sheets", "v4", credentials=credentials)
