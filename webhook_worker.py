from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any

from database import (
    claim_next_webhook_event,
    get_due_sheets_events,
    get_supabase_client,
    reset_stale_webhook_events,
    retry_sheets_events,
    retry_webhook_event,
    update_webhook_events,
)
from google_sheets import sync_all_to_google_sheets

logger = logging.getLogger(__name__)
_worker_lock = threading.Lock()
_worker_thread: threading.Thread | None = None
_worker_wakeup = threading.Event()
_IDLE_POLL_SECONDS = 15


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def process_one_job(client: Any | None = None) -> bool:
    db_client = client or get_supabase_client()
    event = claim_next_webhook_event(db_client)
    if event:
        try:
            from whapi_webhook import process_whapi_case

            process_whapi_case(event["payload"])
        except Exception as exc:
            retry_webhook_event(db_client, event, exc)
            logger.error(
                "Whapi inbox processing failed for event %s (%s)",
                event.get("event_key"),
                type(exc).__name__,
            )
        else:
            update_webhook_events(
                db_client,
                [event["event_key"]],
                "sheets_pending",
                next_attempt_at=_utc_now(),
                last_error=None,
            )
        return True

    sheets_events = get_due_sheets_events(db_client)
    if not sheets_events:
        return False

    try:
        sync_all_to_google_sheets()
    except Exception as exc:
        retry_sheets_events(db_client, sheets_events, exc)
        logger.error(
            "Google Sheets sync failed for %s queued event(s) (%s)",
            len(sheets_events),
            type(exc).__name__,
        )
    else:
        update_webhook_events(
            db_client,
            [event["event_key"] for event in sheets_events],
            "complete",
            last_error=None,
        )
    return True


def _worker_loop() -> None:
    try:
        reset_stale_webhook_events(get_supabase_client())
    except Exception:
        logger.exception("Could not recover stale Whapi inbox events")
    last_recovery = time.monotonic()

    while True:
        if time.monotonic() - last_recovery >= 60:
            try:
                reset_stale_webhook_events(get_supabase_client())
            except Exception:
                logger.exception("Could not recover stale Whapi inbox events")
            last_recovery = time.monotonic()
        try:
            did_work = process_one_job()
        except Exception:
            logger.exception("Background webhook worker iteration failed")
            did_work = False
        if not did_work:
            _worker_wakeup.wait(_IDLE_POLL_SECONDS)
            _worker_wakeup.clear()


def start_worker() -> None:
    global _worker_thread
    with _worker_lock:
        if _worker_thread is None or not _worker_thread.is_alive():
            _worker_thread = threading.Thread(target=_worker_loop, name="whapi-inbox-worker", daemon=True)
            _worker_thread.start()


def wake_worker() -> None:
    _worker_wakeup.set()