import io
import json
from contextlib import redirect_stdout

from ai_processor import CaseInterpretation
import whapi_webhook
from whapi_webhook import app


VALID_GROUP_MESSAGE = {
    "messages": [
        {
            "id": "msg_123",
            "from_me": False,
            "type": "text",
            "timestamp": 1790577718,
            "chat_name": "Test group",
            "chat_id": "120363431619768061@g.us",
            "from": "919289383676",
            "text": {"body": "Webhook_test_1"},
            "from_name": "Suyash",
        }
    ],
    "event": {"type": "messages", "event": "post"},
    "channel_id": "channel_abc",
}


def test_valid_incoming_group_text_message(monkeypatch):
    monkeypatch.setenv("WHAPI_GROUP_ID", "120363431619768061@g.us")
    monkeypatch.setenv("WHAPI_CHANNEL_ID", "channel_abc")

    client = app.test_client()
    capture = io.StringIO()

    with redirect_stdout(capture):
        response = client.post("/webhook/whapi", json=VALID_GROUP_MESSAGE)

    assert response.status_code == 200
    printed = capture.getvalue().strip()
    assert printed
    payload = json.loads(printed)
    assert payload == {
        "message_id": "msg_123",
        "timestamp": 1790577718,
        "sender_id": "919289383676",
        "sender_name": "Suyash",
        "chat_id": "120363431619768061@g.us",
        "chat_name": "Test group",
        "message": "Webhook_test_1",
        "source": "whapi",
    }


def test_process_whapi_case_saves_interpretation(monkeypatch, caplog):
    monkeypatch.setenv("WHAPI_GROUP_ID", "120363431619768061@g.us")
    monkeypatch.setenv("WHAPI_CHANNEL_ID", "channel_abc")

    normalized = {
        "message_id": "msg_999",
        "timestamp": 1790577718,
        "sender_id": "919289383676",
        "sender_name": "Suyash",
        "chat_id": "120363431619768061@g.us",
        "chat_name": "Test group",
        "message": "Covering 9999 Install Pipeline",
        "source": "whapi",
    }
    case = {
        "covering_number": "9999",
        "messages": [{
            "timestamp": "2026-09-28T06:41:58",
            "sender": "Suyash",
            "text": "Covering 9999 Install Pipeline",
        }],
        "participants": ["Suyash"],
        "first_seen": "2026-09-28T06:41:58",
        "last_seen": "2026-09-28T06:41:58",
    }
    interpretation = CaseInterpretation.model_validate({
        "covering_number": "9999",
        "description": "Install Pipeline",
        "urgency": "NORMAL",
        "participants": [{"person": "Suyash", "status": "OK", "last_message": "Okay"}],
        "overall_status": "APPROVED",
    })

    calls = []
    events = []

    monkeypatch.setattr(whapi_webhook, "extract_covering_cases", lambda messages: [case])
    monkeypatch.setattr(whapi_webhook, "interpret_cases", lambda cases: [interpretation])
    monkeypatch.setattr(whapi_webhook, "get_supabase_client", lambda: object())

    def fake_save_case(client, payload, interpretation_arg):
        calls.append({"client": client, "payload": payload, "interpretation": interpretation_arg})
        events.append("save")
        return {"id": "case-9999", "covering_number": "9999"}

    monkeypatch.setattr(whapi_webhook, "save_case", fake_save_case)

    def fake_sheet_sync():
        events.append("sync")
        raise RuntimeError("Sheets unavailable")

    monkeypatch.setattr(whapi_webhook, "sync_all_to_google_sheets", fake_sheet_sync)

    result = whapi_webhook.process_whapi_case(normalized)

    assert result == [interpretation]
    assert len(calls) == 1
    assert calls[0]["payload"]["covering_number"] == "9999"
    assert calls[0]["payload"]["first_seen"] == "2026-09-28T06:41:58"
    assert calls[0]["payload"]["last_seen"] == "2026-09-28T06:41:58"
    assert calls[0]["payload"]["messages"][0]["text"] == "Covering 9999 Install Pipeline"
    assert calls[0]["interpretation"] == interpretation
    assert events == ["save", "sync"]
    assert "Google Sheets sync failed after saving covering 9999" in caplog.text


def test_from_me_true_is_ignored(monkeypatch):
    monkeypatch.setenv("WHAPI_GROUP_ID", "120363431619768061@g.us")
    monkeypatch.setenv("WHAPI_CHANNEL_ID", "channel_abc")

    payload = {
        **VALID_GROUP_MESSAGE,
        "messages": [{
            **VALID_GROUP_MESSAGE["messages"][0],
            "from_me": True,
        }],
    }

    client = app.test_client()
    capture = io.StringIO()
    with redirect_stdout(capture):
        response = client.post("/webhook/whapi", json=payload)

    assert response.status_code == 200
    assert capture.getvalue().strip() == ""


def test_wrong_chat_id_is_ignored(monkeypatch):
    monkeypatch.setenv("WHAPI_GROUP_ID", "different-group@g.us")
    monkeypatch.setenv("WHAPI_CHANNEL_ID", "channel_abc")

    client = app.test_client()
    capture = io.StringIO()
    with redirect_stdout(capture):
        response = client.post("/webhook/whapi", json=VALID_GROUP_MESSAGE)

    assert response.status_code == 200
    assert capture.getvalue().strip() == ""


def test_non_text_message_is_ignored(monkeypatch):
    monkeypatch.setenv("WHAPI_GROUP_ID", "120363431619768061@g.us")
    monkeypatch.setenv("WHAPI_CHANNEL_ID", "channel_abc")

    payload = {
        **VALID_GROUP_MESSAGE,
        "messages": [{
            **VALID_GROUP_MESSAGE["messages"][0],
            "type": "image",
            "text": {"body": "ignored"},
        }],
    }

    client = app.test_client()
    capture = io.StringIO()
    with redirect_stdout(capture):
        response = client.post("/webhook/whapi", json=payload)

    assert response.status_code == 200
    assert capture.getvalue().strip() == ""


def test_wrong_event_type_is_ignored(monkeypatch):
    monkeypatch.setenv("WHAPI_GROUP_ID", "120363431619768061@g.us")
    monkeypatch.setenv("WHAPI_CHANNEL_ID", "channel_abc")

    payload = {
        **VALID_GROUP_MESSAGE,
        "event": {"type": "status", "event": "post"},
    }

    client = app.test_client()
    capture = io.StringIO()
    with redirect_stdout(capture):
        response = client.post("/webhook/whapi", json=payload)

    assert response.status_code == 200
    assert capture.getvalue().strip() == ""


def test_malformed_payload_is_handled_safely(monkeypatch):
    monkeypatch.setenv("WHAPI_GROUP_ID", "120363431619768061@g.us")
    monkeypatch.setenv("WHAPI_CHANNEL_ID", "channel_abc")

    client = app.test_client()
    capture = io.StringIO()
    with redirect_stdout(capture):
        response = client.post(
            "/webhook/whapi",
            data='{"messages": [',
            content_type="application/json",
        )

    assert response.status_code == 200
    assert capture.getvalue().strip() == ""
