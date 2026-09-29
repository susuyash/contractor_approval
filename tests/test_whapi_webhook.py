import threading
import time

import pytest

from ai_processor import CaseInterpretation
import whapi_webhook
import webhook_worker
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
            "text": {"body": "Covering 1234 contractor payment"},
            "from_name": "Suyash",
        }
    ],
    "event": {"type": "messages", "event": "post"},
    "channel_id": "channel_abc",
}


@pytest.fixture(autouse=True)
def configure_webhook(monkeypatch):
    monkeypatch.setattr(whapi_webhook, "start_worker", lambda: None)


def post_webhook(client, payload, **kwargs):
    return client.post("/webhook/whapi", json=payload, **kwargs)


def test_valid_incoming_group_text_message(monkeypatch):
    monkeypatch.setenv("WHAPI_GROUP_ID", "120363431619768061@g.us")
    monkeypatch.setenv("WHAPI_CHANNEL_ID", "channel_abc")

    client = app.test_client()
    enqueued = []
    monkeypatch.setattr(whapi_webhook, "get_supabase_client", lambda: object())
    monkeypatch.setattr(
        whapi_webhook,
        "enqueue_webhook_message",
        lambda db_client, event_key, payload: enqueued.append((event_key, payload)) or True,
    )
    monkeypatch.setattr(whapi_webhook, "wake_worker", lambda: None)
    response = post_webhook(client, VALID_GROUP_MESSAGE)

    assert response.status_code == 200
    assert enqueued[0][0] == "120363431619768061@g.us:msg_123"
    assert enqueued[0][1]["message"] == "Covering 1234 contractor payment"


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
    monkeypatch.setattr(whapi_webhook, "extract_covering_cases", lambda messages: [case])
    monkeypatch.setattr(whapi_webhook, "interpret_cases", lambda cases: [interpretation])
    monkeypatch.setattr(whapi_webhook, "get_supabase_client", lambda: object())

    def fake_save_case(client, payload, interpretation_arg):
        calls.append({"client": client, "payload": payload, "interpretation": interpretation_arg})
        return {"id": "case-9999", "covering_number": "9999"}

    monkeypatch.setattr(whapi_webhook, "save_case", fake_save_case)

    result = whapi_webhook.process_whapi_case(normalized)

    assert result == [interpretation]
    assert len(calls) == 1
    assert calls[0]["payload"]["covering_number"] == "9999"
    assert calls[0]["payload"]["first_seen"] == "2026-09-28T06:41:58"
    assert calls[0]["payload"]["last_seen"] == "2026-09-28T06:41:58"
    assert calls[0]["payload"]["messages"][0]["text"] == "Covering 9999 Install Pipeline"
    assert calls[0]["interpretation"] == interpretation
    assert calls[0]["payload"]["raised_by"] == "Suyash"


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
    response = post_webhook(client, payload)

    assert response.status_code == 200


def test_wrong_chat_id_is_ignored(monkeypatch):
    monkeypatch.setenv("WHAPI_GROUP_ID", "different-group@g.us")
    monkeypatch.setenv("WHAPI_CHANNEL_ID", "channel_abc")

    client = app.test_client()
    response = post_webhook(client, VALID_GROUP_MESSAGE)

    assert response.status_code == 200


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
    response = post_webhook(client, payload)

    assert response.status_code == 200


def test_wrong_event_type_is_ignored(monkeypatch):
    monkeypatch.setenv("WHAPI_GROUP_ID", "120363431619768061@g.us")
    monkeypatch.setenv("WHAPI_CHANNEL_ID", "channel_abc")

    payload = {
        **VALID_GROUP_MESSAGE,
        "event": {"type": "status", "event": "post"},
    }

    client = app.test_client()
    response = post_webhook(client, payload)

    assert response.status_code == 200


def test_malformed_payload_is_handled_safely(monkeypatch):
    monkeypatch.setenv("WHAPI_GROUP_ID", "120363431619768061@g.us")
    monkeypatch.setenv("WHAPI_CHANNEL_ID", "channel_abc")

    client = app.test_client()
    response = post_webhook(
        client,
        None,
        data='{"messages": [',
        content_type="application/json",
    )

    assert response.status_code == 200


def test_ordinary_group_chatter_is_not_enqueued(monkeypatch):
    monkeypatch.setenv("WHAPI_GROUP_ID", "120363431619768061@g.us")
    monkeypatch.setattr(whapi_webhook, "enqueue_webhook_message", lambda *_args: pytest.fail("chatter enqueued"))
    monkeypatch.setattr(whapi_webhook, "get_supabase_client", lambda: pytest.fail("chatter accessed Supabase"))
    monkeypatch.setattr(whapi_webhook, "interpret_cases", lambda *_args: pytest.fail("chatter invoked AI"))
    monkeypatch.setattr(whapi_webhook, "start_worker", lambda: pytest.fail("chatter started worker"))
    payload = {
        **VALID_GROUP_MESSAGE,
        "messages": [{
            **VALID_GROUP_MESSAGE["messages"][0],
            "text": {"body": "claude bna deta abhi tak"},
        }],
    }

    response = post_webhook(app.test_client(), payload)

    assert response.status_code == 200


def test_duplicate_delivery_uses_same_durable_event_key(monkeypatch):
    monkeypatch.setenv("WHAPI_GROUP_ID", "120363431619768061@g.us")
    keys = set()
    inserted = []

    def enqueue(_client, event_key, _payload):
        inserted.append(event_key)
        if event_key in keys:
            return False
        keys.add(event_key)
        return True

    monkeypatch.setattr(whapi_webhook, "get_supabase_client", lambda: object())
    monkeypatch.setattr(whapi_webhook, "enqueue_webhook_message", enqueue)
    monkeypatch.setattr(whapi_webhook, "wake_worker", lambda: None)
    client = app.test_client()

    assert post_webhook(client, VALID_GROUP_MESSAGE).status_code == 200
    assert post_webhook(client, VALID_GROUP_MESSAGE).status_code == 200
    assert inserted == ["120363431619768061@g.us:msg_123"] * 2
    assert len(keys) == 1


def test_webhook_returns_without_waiting_for_worker(monkeypatch):
    monkeypatch.setenv("WHAPI_GROUP_ID", "120363431619768061@g.us")
    monkeypatch.setattr(whapi_webhook, "get_supabase_client", lambda: object())
    monkeypatch.setattr(whapi_webhook, "enqueue_webhook_message", lambda *_args: True)
    worker_started = threading.Event()

    def slow_sheets_sync():
        worker_started.set()
        time.sleep(2)

    monkeypatch.setattr(webhook_worker, "sync_all_to_google_sheets", slow_sheets_sync)
    monkeypatch.setattr(webhook_worker, "_worker_loop", slow_sheets_sync)
    monkeypatch.setattr(webhook_worker, "_worker_thread", None)
    monkeypatch.setattr(whapi_webhook, "start_worker", webhook_worker.start_worker)
    monkeypatch.setattr(whapi_webhook, "wake_worker", webhook_worker.wake_worker)
    started_at = time.perf_counter()
    response = post_webhook(app.test_client(), VALID_GROUP_MESSAGE)
    elapsed = time.perf_counter() - started_at

    assert response.status_code == 200
    assert elapsed < 1
    assert worker_started.wait(timeout=0.5)


def test_inbox_failure_returns_retryable_status_without_logging_secrets(monkeypatch, caplog):
    monkeypatch.setenv("WHAPI_GROUP_ID", "120363431619768061@g.us")
    monkeypatch.setattr(whapi_webhook, "get_supabase_client", lambda: object())
    monkeypatch.setattr(
        whapi_webhook,
        "enqueue_webhook_message",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("database unavailable")),
    )

    response = post_webhook(app.test_client(), VALID_GROUP_MESSAGE)

    assert response.status_code == 503
