import whapi_webhook
import webhook_worker


def test_worker_persists_case_before_scheduling_sheets(monkeypatch):
    event = {"event_key": "group:message-1", "payload": {"message": "Covering 1234"}, "attempts": 1}
    saved = []
    updates = []
    claimed = iter([event, None])

    monkeypatch.setattr(webhook_worker, "claim_next_webhook_event", lambda _client: next(claimed))
    monkeypatch.setattr(webhook_worker, "get_due_sheets_events", lambda _client: [])
    monkeypatch.setattr(whapi_webhook, "process_whapi_case", lambda payload: saved.append(payload))
    monkeypatch.setattr(
        webhook_worker,
        "update_webhook_events",
        lambda _client, keys, status, **kwargs: updates.append((keys, status, kwargs)),
    )

    assert webhook_worker.process_one_job(object()) is True
    assert saved == [event["payload"]]
    assert updates[0][0:2] == ([event["event_key"]], "sheets_pending")


def test_sheets_timeout_keeps_persisted_event_retryable(monkeypatch, caplog):
    event = {"event_key": "group:message-2", "payload": {"message": "Covering 5678"}, "attempts": 1}
    persisted = []
    retries = []
    claimed = iter([event, None])
    due_events = iter([[event]])

    monkeypatch.setattr(webhook_worker, "claim_next_webhook_event", lambda _client: next(claimed))
    monkeypatch.setattr(webhook_worker, "get_due_sheets_events", lambda _client: next(due_events))
    monkeypatch.setattr(whapi_webhook, "process_whapi_case", lambda payload: persisted.append(payload))
    monkeypatch.setattr(webhook_worker, "sync_all_to_google_sheets", lambda: (_ for _ in ()).throw(TimeoutError("Sheets timed out")))
    monkeypatch.setattr(
        webhook_worker,
        "update_webhook_events",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        webhook_worker,
        "retry_sheets_events",
        lambda _client, events, error: retries.append((events, error)),
    )

    assert webhook_worker.process_one_job(object()) is True
    assert persisted == [event["payload"]]
    assert webhook_worker.process_one_job(object()) is True
    assert len(retries) == 1
    assert retries[0][0] == [event]
    assert isinstance(retries[0][1], TimeoutError)
    assert "Google Sheets sync failed" in caplog.text


def test_ai_or_database_failure_returns_inbox_event_for_retry(monkeypatch, caplog):
    event = {"event_key": "group:message-3", "payload": {"message": "Covering 9012"}, "attempts": 2}
    claimed = iter([event])
    retries = []

    monkeypatch.setattr(webhook_worker, "claim_next_webhook_event", lambda _client: next(claimed))
    monkeypatch.setattr(whapi_webhook, "process_whapi_case", lambda _payload: (_ for _ in ()).throw(RuntimeError("AI unavailable")))
    monkeypatch.setattr(webhook_worker, "retry_webhook_event", lambda _client, item, error: retries.append((item, error)))

    assert webhook_worker.process_one_job(object()) is True
    assert retries[0][0] == event
    assert "Whapi inbox processing failed" in caplog.text