import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from ai_processor import (
    CaseInterpretation,
    OverallStatus,
    ParticipantStatus,
    UrgencyLevel,
    interpret_covering_case,
)


class FakeGroqResponse:
    def __init__(self, payload):
        self.choices = [
            SimpleNamespace(
                message=SimpleNamespace(content=json.dumps(payload)),
                finish_reason="stop",
            )
        ]


def test_everyone_approved():
    case = {
        "covering_number": "259",
        "messages": [
            {"timestamp": "2024-01-02T09:00:00", "sender": "Jay", "text": "COVERING NO_259.pdf"},
            {"timestamp": "2024-01-02T09:01:00", "sender": "Mehta", "text": "Urgent payment Joyti built mart Covering no 259 ok"},
            {"timestamp": "2024-01-02T09:02:00", "sender": "Rishabh", "text": "259 okay"},
            {"timestamp": "2024-01-02T09:03:00", "sender": "Jay", "text": "Approved"},
        ],
        "participants": ["Jay", "Mehta", "Rishabh"],
        "first_seen": "2024-01-02T09:00:00",
        "last_seen": "2024-01-02T09:03:00",
    }

    response = FakeGroqResponse(
        {
            "covering_number": "259",
            "description": "contractor payment",
            "urgency": "URGENT",
            "participants": [
                {"person": "Jay", "status": "APPROVED", "last_message": "Approved"},
                {"person": "Mehta", "status": "OK", "last_message": "ok"},
                {"person": "Rishabh", "status": "OK", "last_message": "okay"},
            ],
            "overall_status": "APPROVED",
        }
    )

    result = interpret_covering_case(case, gemini_client=response)

    assert isinstance(result, CaseInterpretation)
    assert result.covering_number == "259"
    assert result.description == "contractor payment"
    assert result.urgency == UrgencyLevel.URGENT
    assert result.overall_status == OverallStatus.APPROVED
    assert {p.person for p in result.participants} == {"Jay", "Mehta", "Rishabh"}


def test_some_people_pending():
    case = {
        "covering_number": "265",
        "messages": [
            {"timestamp": "2024-01-03T08:00:00", "sender": "Jay", "text": "Please approve contractor payment covering no 265 for urgent release of payment"},
            {"timestamp": "2024-01-03T08:05:00", "sender": "Naveen", "text": "will check"},
            {"timestamp": "2024-01-03T08:10:00", "sender": "Jay", "text": "Approved"},
        ],
        "participants": ["Jay", "Naveen"],
        "first_seen": "2024-01-03T08:00:00",
        "last_seen": "2024-01-03T08:10:00",
    }

    response = FakeGroqResponse(
        {
            "covering_number": "265",
            "description": "contractor payment",
            "urgency": "URGENT",
            "participants": [
                {"person": "Jay", "status": "APPROVED", "last_message": "Approved"},
                {"person": "Naveen", "status": "PENDING", "last_message": "will check"},
            ],
            "overall_status": "PENDING",
        }
    )

    result = interpret_covering_case(case, gemini_client=response)

    assert result.overall_status == OverallStatus.PENDING
    assert [p.status for p in result.participants] == [ParticipantStatus.APPROVED, ParticipantStatus.PENDING]


def test_rejected_request():
    case = {
        "covering_number": "300",
        "messages": [
            {"timestamp": "2024-01-04T10:00:00", "sender": "Jay", "text": "Covering no 300"},
            {"timestamp": "2024-01-04T10:05:00", "sender": "Manager", "text": "Rejected"},
        ],
        "participants": ["Jay", "Manager"],
        "first_seen": "2024-01-04T10:00:00",
        "last_seen": "2024-01-04T10:05:00",
    }

    response = FakeGroqResponse(
        {
            "covering_number": "300",
            "description": "contractor payment",
            "urgency": "UNKNOWN",
            "participants": [
                {"person": "Manager", "status": "REJECTED", "last_message": "Rejected"},
            ],
            "overall_status": "REJECTED",
        }
    )

    result = interpret_covering_case(case, gemini_client=response)

    assert result.overall_status == OverallStatus.REJECTED
    assert result.urgency == UrgencyLevel.UNKNOWN


def test_ambiguous_response_uses_unknown():
    case = {
        "covering_number": "777",
        "messages": [
            {"timestamp": "2024-01-05T12:00:00", "sender": "Jay", "text": "Covering no 777"},
            {"timestamp": "2024-01-05T12:01:00", "sender": "Alex", "text": "Maybe"},
        ],
        "participants": ["Jay", "Alex"],
        "first_seen": "2024-01-05T12:00:00",
        "last_seen": "2024-01-05T12:01:00",
    }

    response = FakeGroqResponse(
        {
            "covering_number": "777",
            "description": "contractor payment",
            "urgency": "UNKNOWN",
            "participants": [
                {"person": "Alex", "status": "UNKNOWN", "last_message": "Maybe"},
            ],
            "overall_status": "UNKNOWN",
        }
    )

    result = interpret_covering_case(case, gemini_client=response)

    assert result.participants[0].status == ParticipantStatus.UNKNOWN
    assert result.overall_status == OverallStatus.UNKNOWN


def test_missing_description_is_null():
    case = {
        "covering_number": "888",
        "messages": [
            {"timestamp": "2024-01-06T09:00:00", "sender": "Jay", "text": "Covering no 888"},
            {"timestamp": "2024-01-06T09:03:00", "sender": "Rahul", "text": "Okay"},
        ],
        "participants": ["Jay", "Rahul"],
        "first_seen": "2024-01-06T09:00:00",
        "last_seen": "2024-01-06T09:03:00",
    }

    response = FakeGroqResponse(
        {
            "covering_number": "888",
            "description": None,
            "urgency": "UNKNOWN",
            "participants": [
                {"person": "Rahul", "status": "OK", "last_message": "Okay"},
            ],
            "overall_status": "APPROVED",
        }
    )

    result = interpret_covering_case(case, gemini_client=response)

    assert result.description is None
    assert result.urgency == UrgencyLevel.UNKNOWN


@pytest.mark.parametrize(
    ("covering_number", "message_text", "expected_person"),
    [
        ("7777", "Covering 7777 approved by deep sir", "Deep Patel"),
        ("8888", "Mehta sir approved 8888", "Mehta Ji SPM Dv Bsp Site"),
        ("8888", "Gagan sir approved 8888", "Gagan Deep Singh"),
    ],
)
def test_explicit_named_approver_overrides_sender_attribution(covering_number, message_text, expected_person):
    case = {
        "covering_number": covering_number,
        "messages": [{"timestamp": "2026-09-29T09:00:00", "sender": "Suyash", "text": message_text}],
    }
    response = FakeGroqResponse(
        {
            "covering_number": covering_number,
            "description": None,
            "urgency": "UNKNOWN",
            "participants": [{"person": "Suyash", "status": "APPROVED", "last_message": message_text}],
            "overall_status": "APPROVED",
        }
    )

    result = interpret_covering_case(case, gemini_client=response)

    assert [(participant.person, participant.status) for participant in result.participants] == [
        (expected_person, ParticipantStatus.APPROVED)
    ]


def test_json_schema_has_explicit_required_and_additional_properties_false():
    schema = CaseInterpretation.model_json_schema()
    participant_schema = schema["$defs"]["ParticipantInterpretation"]

    assert schema.get("additionalProperties") is False
    assert participant_schema.get("additionalProperties") is False

    assert set(schema["required"]) == set(schema["properties"]) - set()
    assert set(schema["required"]) == {"covering_number", "description", "urgency", "participants", "overall_status"}
    assert set(participant_schema["required"]) == {"person", "status", "last_message"}


def test_invalid_ai_status_values_are_rejected():
    with pytest.raises(ValidationError):
        CaseInterpretation.model_validate(
            {
                "covering_number": "123",
                "description": "contractor payment",
                "urgency": "URGENT",
                "participants": [{"person": "Jay", "status": "INVALID", "last_message": "Okay"}],
                "overall_status": "APPROVED",
            }
        )

    with pytest.raises(ValidationError):
        CaseInterpretation.model_validate(
            {
                "covering_number": "123",
                "description": "contractor payment",
                "urgency": "URGENT",
                "participants": [{"person": "Jay", "status": "OK", "last_message": "Okay"}],
                "overall_status": "INVALID",
            }
        )
