# Contractor Approval Tracker

WhatsApp group messages are filtered for covering-number relevance, durably queued in Supabase, interpreted by Groq, persisted as cases/participants/messages, then reflected in Google Sheets. Supabase remains the source of truth; the Cases and Case History tabs are derived views.

## Production setup

1. Apply `supabase_schema.sql` in Supabase. It adds the `whapi_inbox` table; it does not delete or rewrite existing case data.
2. Configure Render environment variables: `SUPABASE_URL`, `SUPABASE_SECRET_KEY`, `WHAPI_GROUP_ID`, `GROQ_API_KEY`, `GROQ_MODEL`, `GOOGLE_SHEETS_SPREADSHEET_ID`, and the Google service-account credential path.
3. Configure the Whapi webhook URL as `/webhook/whapi`.
4. Keep the service on one Gunicorn worker for the in-process queue worker. The worker starts after a health check or relevant webhook request, processes durable inbox rows, and retries failed processing or Sheets updates. Render restarts do not erase queued rows; stale processing leases are reclaimed after five minutes.

The webhook only performs authentication, group/message filtering, deterministic covering relevance detection, and a durable Supabase inbox insert before acknowledging a relevant event. Ordinary chatter does not invoke Groq or database case processing. If the inbox insert fails, the endpoint responds `503` so Whapi can retry; no server can durably preserve an event while Supabase itself is unavailable.

Groq has a 15-second request timeout and automatic retries are disabled. A Supabase inbox event is retained until case processing succeeds and its Sheets synchronization completes. Sheets failures are logged and retried with backoff.

Sheets sync reads cases, participants, and messages in three bulk queries, builds both tabs in memory, writes replacement values before trimming any old tail rows, and performs legacy-tab cleanup only after both required tabs have been written. A failed write therefore does not begin by clearing the existing sheet. The first successful sync removes legacy `Sheet1`, `Participants`, or `Messages` tabs after the Cases and Case History tabs contain data.

The webhook only associates messages with a case when the message text contains a covering number. The current checked-in Whapi payload handling has no reply-context contract, so reply-only messages such as `Approved` remain ignored; include the covering number until reply metadata is verified and explicitly supported.

## Local setup and tests

```bash
python -m venv .venv
pip install -r requirements.txt
pytest tests -q
```
