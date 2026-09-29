import pytest
from pydantic import ValidationError

from ai_processor import CaseInterpretation, OverallStatus, ParticipantInterpretation, ParticipantStatus
from database import (
    get_cases,
    get_messages,
    get_participants,
    get_supabase_client,
    insert_message,
    load_environment,
    save_case,
    upsert_case,
    upsert_participant,
    get_or_create_case,
)


class FakeQueryResult:
    def __init__(self, data):
        self.data = data

    def __getitem__(self, key):
        return self.data[key]

    def __iter__(self):
        return iter(self.data)

    def __len__(self):
        return len(self.data)

    def execute(self):
        return self


class FakeTable:
    def __init__(self, records=None):
        self.records = records or []
        self.last_insert = None
        self.last_upsert = None
        self._filters = []

    def insert(self, payload):
        self.last_insert = payload
        inserted = {**payload, "id": "new-id"}
        self.records.append(inserted)
        return FakeQueryResult([inserted])

    def upsert(self, payload, on_conflict=None):
        self.last_upsert = payload
        self._filters = []
        existing = next((item for item in self.records if item.get("covering_number") == payload.get("covering_number")), None)
        if existing:
            existing.update(payload)
            return FakeQueryResult([existing])
        inserted = {**payload, "id": "new-id"}
        self.records.append(inserted)
        return FakeQueryResult([inserted])

    def select(self, *_args, **_kwargs):
        self._filters = []
        return self

    def eq(self, field, value):
        self._filters.append((field, value))
        return self

    def order(self, *_args, **_kwargs):
        return self

    def execute(self):
        filtered = list(self.records)
        for field, value in getattr(self, "_filters", []):
            filtered = [item for item in filtered if item.get(field) == value]
        return FakeQueryResult(filtered)


class FakeClient:
    def __init__(self):
        self.cases = FakeTable([
            {
                "id": "case-1",
                "covering_number": "259",
                "description": "contractor payment",
                "urgency": "URGENT",
                "raised_by": "Jay",
                "overall_status": OverallStatus.APPROVED.value,
                "first_seen": "2024-01-02T09:00:00",
                "last_seen": "2024-01-02T09:03:00",
                "created_at": "2024-01-02T09:00:00",
                "updated_at": "2024-01-02T09:03:00",
            }
        ])
        self.participants = FakeTable([])
        self.messages = FakeTable([])

    def table(self, name):
        if name == "cases":
            return self.cases
        if name == "participants":
            return self.participants
        if name == "messages":
            return self.messages
        raise ValueError(name)


def test_creates_new_covering_case():
    client = FakeClient()
    case_payload = {
        "covering_number": "300",
        "description": "contractor payment",
        "urgency": "URGENT",
        "overall_status": OverallStatus.APPROVED.value,
        "first_seen": "2024-01-04T10:00:00",
        "last_seen": "2024-01-04T10:05:00",
        "participants": [{"person": "Jay", "status": ParticipantStatus.APPROVED.value, "last_message": "Approved"}],
        "messages": [{"timestamp": "2024-01-04T10:05:00", "sender": "Jay", "text": "Approved"}],
    }

    result = save_case(client, case_payload)

    assert result["covering_number"] == "300"
    assert client.cases.records[1]["covering_number"] == "300"


def test_updates_existing_covering_case():
    client = FakeClient()
    case_payload = {
        "covering_number": "259",
        "description": "updated description",
        "urgency": "NORMAL",
        "overall_status": OverallStatus.PENDING.value,
        "first_seen": "2024-01-02T09:00:00",
        "last_seen": "2024-01-02T09:10:00",
    }

    result = upsert_case(client, case_payload)

    assert result["covering_number"] == "259"
    assert result["overall_status"] == OverallStatus.PENDING.value


def test_saves_participants():
    client = FakeClient()
    participant = {"person": "Rishabh", "status": ParticipantStatus.PENDING.value, "last_message": "will check"}

    result = upsert_participant(client, "case-1", participant)

    assert result["person"] == "Rishabh"
    assert result["status"] == ParticipantStatus.PENDING.value


def test_saves_messages():
    client = FakeClient()
    message = {"sender": "Jay", "text": "Approved", "timestamp": "2024-01-02T09:03:00"}

    result = insert_message(client, "case-1", message)

    assert result["sender"] == "Jay"
    assert result["message"] == "Approved"


def test_reprocessing_same_case_does_not_duplicate():
    client = FakeClient()
    case_payload = {
        "covering_number": "259",
        "description": "contractor payment",
        "urgency": "URGENT",
        "overall_status": OverallStatus.APPROVED.value,
        "messages": [{"timestamp": "2024-01-02T09:00:00", "sender": "Jay", "text": "Approved"}],
    }

    save_case(client, case_payload)
    save_case(client, case_payload)

    case_records = [item for item in client.cases.records if item.get("covering_number") == "259"]
    assert len(case_records) == 1


def test_accepts_enum_values_from_ai_model_dump():
    client = FakeClient()
    participant = {"person": "Jay", "status": ParticipantStatus.APPROVED, "last_message": "Approved"}

    result = upsert_participant(client, "case-1", participant)

    assert result["status"] == ParticipantStatus.APPROVED.value


def test_one_approval_keeps_case_pending():
    client = FakeClient()
    case_payload = {
        "covering_number": "401",
        "description": "contractor payment",
        "urgency": "NORMAL",
        "participants": [{"person": "Mukesh", "status": ParticipantStatus.APPROVED.value, "last_message": "Approved"}],
        "messages": [{"timestamp": "2024-01-02T09:00:00", "sender": "Mukesh", "text": "Approved"}],
    }

    result = save_case(client, case_payload)

    assert result["overall_status"] == OverallStatus.PENDING.value


@pytest.mark.parametrize("person", ["Suyash", "Deep Patel"])
def test_ai_approved_with_only_one_participant_persists_pending(person):
    client = FakeClient()
    interpretation = CaseInterpretation(
        covering_number="405",
        description="Covering 405 approved",
        participants=[ParticipantInterpretation(person=person, status=ParticipantStatus.APPROVED, last_message="Approved")],
        overall_status=OverallStatus.APPROVED,
    )

    result = save_case(
        client,
        {
            "covering_number": "405",
            "participants": [
                {"person": person, "status": ParticipantStatus.APPROVED.value, "last_message": "Approved"}
            ],
        },
        interpretation,
    )

    assert result["overall_status"] == OverallStatus.PENDING.value
    assert client.cases.last_upsert["overall_status"] == OverallStatus.PENDING.value


def test_existing_approved_case_is_downgraded_when_only_one_required_approves():
    client = FakeClient()
    case_payload = {
        "covering_number": "259",
        "participants": [
            {"person": "Mukesh", "status": ParticipantStatus.APPROVED.value, "last_message": "Approved"}
        ],
    }

    result = save_case(client, case_payload)

    assert result["overall_status"] == OverallStatus.PENDING.value
    assert client.cases.last_upsert["overall_status"] == OverallStatus.PENDING.value


def test_four_approvals_keeps_case_pending():
    client = FakeClient()
    case_payload = {
        "covering_number": "402",
        "description": "contractor payment",
        "urgency": "NORMAL",
        "participants": [
            {"person": "Mukesh", "status": ParticipantStatus.APPROVED.value, "last_message": "Approved"},
            {"person": "Mehta Ji SPM Dv Bsp Site", "status": ParticipantStatus.APPROVED.value, "last_message": "Approved"},
            {"person": "Jagga Rao", "status": ParticipantStatus.APPROVED.value, "last_message": "Approved"},
            {"person": "Gagan Deep Singh", "status": ParticipantStatus.APPROVED.value, "last_message": "Approved"},
        ],
        "messages": [{"timestamp": "2024-01-02T09:00:00", "sender": name, "text": "Approved"} for name in ["Mukesh", "Mehta Ji SPM Dv Bsp Site", "Jagga Rao", "Gagan Deep Singh"]],
    }

    result = save_case(client, case_payload)

    assert result["overall_status"] == OverallStatus.PENDING.value


def test_all_five_required_approvals_approve_case():
    client = FakeClient()
    case_payload = {
        "covering_number": "403",
        "description": "contractor payment",
        "urgency": "NORMAL",
        "participants": [
            {"person": "Mukesh", "status": ParticipantStatus.APPROVED.value, "last_message": "Approved"},
            {"person": "Mehta Ji SPM Dv Bsp Site", "status": ParticipantStatus.APPROVED.value, "last_message": "Approved"},
            {"person": "Jagga Rao", "status": ParticipantStatus.APPROVED.value, "last_message": "Approved"},
            {"person": "Gagan Deep Singh", "status": ParticipantStatus.APPROVED.value, "last_message": "Approved"},
            {"person": "Deep Patel", "status": ParticipantStatus.APPROVED.value, "last_message": "Approved"},
        ],
        "messages": [{"timestamp": f"2024-01-02T09:{i:02d}:00", "sender": person, "text": "Approved"} for i, person in enumerate([
            "Mukesh",
            "Mehta Ji SPM Dv Bsp Site",
            "Jagga Rao",
            "Gagan Deep Singh",
            "Deep Patel",
        ])],
    }

    result = save_case(client, case_payload)

    assert result["overall_status"] == OverallStatus.APPROVED.value


def test_duplicate_approvals_do_not_count_twice():
    client = FakeClient()
    case_payload = {
        "covering_number": "404",
        "description": "contractor payment",
        "urgency": "NORMAL",
        "participants": [
            {"person": "Mukesh", "status": ParticipantStatus.APPROVED.value, "last_message": "Approved"},
            {"person": "Mukesh", "status": ParticipantStatus.APPROVED.value, "last_message": "Approved again"},
            {"person": "Mehta Ji SPM Dv Bsp Site", "status": ParticipantStatus.APPROVED.value, "last_message": "Approved"},
            {"person": "Jagga Rao", "status": ParticipantStatus.APPROVED.value, "last_message": "Approved"},
            {"person": "Gagan Deep Singh", "status": ParticipantStatus.APPROVED.value, "last_message": "Approved"},
            {"person": "Deep Patel", "status": ParticipantStatus.APPROVED.value, "last_message": "Approved"},
        ],
        "messages": [{"timestamp": f"2024-01-02T09:{i:02d}:00", "sender": person, "text": "Approved"} for i, person in enumerate([
            "Mukesh",
            "Mukesh",
            "Mehta Ji SPM Dv Bsp Site",
            "Jagga Rao",
            "Gagan Deep Singh",
            "Deep Patel",
        ])],
    }

    result = save_case(client, case_payload)

    assert result["overall_status"] == OverallStatus.APPROVED.value


def test_invalid_status_values_are_rejected():
    with pytest.raises(ValueError):
        upsert_participant(FakeClient(), "case-1", {"person": "Jay", "status": "INVALID", "last_message": "Okay"})

    with pytest.raises(ValueError):
        save_case(FakeClient(), {"covering_number": "123", "overall_status": "INVALID"})


def test_missing_optional_description_is_handled():
    client = FakeClient()
    case_payload = {
        "covering_number": "444",
        "description": None,
        "overall_status": OverallStatus.UNKNOWN.value,
    }
    result = save_case(client, case_payload)
    assert result["covering_number"] == "444"
    assert result["description"] is None


def test_environment_variables_are_required_and_not_printed(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "")
    with pytest.raises(ValueError):
        load_environment()


def test_get_supabase_client_reuses_client(monkeypatch):
    client = object()
    created = []
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "secret")
    monkeypatch.setattr("database._supabase_client", None)
    monkeypatch.setattr("database._supabase_client_config", None)
    monkeypatch.setattr("database.create_client", lambda url, key: created.append((url, key)) or client)

    assert get_supabase_client() is client
    assert get_supabase_client() is client
    assert created == [("https://example.supabase.co", "secret")]


def test_database_reads_keep_existing_results_with_explicit_client(monkeypatch):
    client = FakeClient()
    client.participants.records = [{"case_id": "case-1", "person": "Jay", "status": "APPROVED"}]
    client.messages.records = [{"case_id": "case-1", "sender": "Jay", "message": "Approved"}]
    monkeypatch.setattr("database.get_supabase_client", lambda: pytest.fail("unexpected client creation"))

    assert get_cases(client=client, status="approved") == client.cases.records
    assert get_participants(client=client, case_id="case-1") == client.participants.records
    assert get_messages(client=client, case_id="case-1") == client.messages.records
