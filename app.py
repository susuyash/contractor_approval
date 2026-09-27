from __future__ import annotations

import streamlit as st

from database import get_case_by_covering_number, get_cases, get_messages, get_participants
from google_sheets import sync_all_to_google_sheets


def summarize_cases(cases: list[dict]) -> dict[str, int]:
    pending = sum(1 for case in cases if str(case.get("overall_status") or "").upper() == "PENDING")
    approved = sum(1 for case in cases if str(case.get("overall_status") or "").upper() == "APPROVED")
    rejected = sum(1 for case in cases if str(case.get("overall_status") or "").upper() == "REJECTED")
    return {
        "total": len(cases),
        "pending": pending,
        "approved": approved,
        "rejected": rejected,
    }


def filter_cases(cases: list[dict], status_filter: str = "All", urgency_filter: str = "All") -> list[dict]:
    filtered = list(cases)

    if status_filter and status_filter != "All":
        normalized = status_filter.upper()
        filtered = [case for case in filtered if str(case.get("overall_status") or "").upper() == normalized]

    if urgency_filter and urgency_filter != "All":
        normalized = urgency_filter.upper()
        filtered = [case for case in filtered if str(case.get("urgency") or "UNKNOWN").upper() == normalized]

    return sorted(
        filtered,
        key=lambda item: str(item.get("updated_at") or item.get("last_seen") or ""),
        reverse=True,
    )


def build_table_rows(cases: list[dict]) -> list[dict]:
    rows = []
    for case in cases:
        rows.append(
            {
                "Covering Number": str(case.get("covering_number") or ""),
                "Description": case.get("description") or "—",
                "Urgency": str(case.get("urgency") or "UNKNOWN").upper(),
                "Overall Status": str(case.get("overall_status") or "UNKNOWN").upper(),
                "Last Updated": case.get("updated_at") or case.get("last_seen") or "—",
            }
        )
    return rows


def _get_case_context(covering_number: str):
    case = get_case_by_covering_number(covering_number=covering_number)
    if not case:
        return None, [], []
    case_id = case.get("id")
    participants = get_participants(case_id=case_id)
    messages = get_messages(case_id=case_id)
    return case, participants, messages


def main() -> None:
    st.set_page_config(page_title="Contractor Payment Tracker", layout="wide")
    st.title("Contractor Payment Tracker")

    cases = get_cases()
    summary = summarize_cases(cases)

    metric_cols = st.columns(4)
    metric_cols[0].metric("Total Cases", summary["total"])
    metric_cols[1].metric("Pending", summary["pending"])
    metric_cols[2].metric("Approved", summary["approved"])
    metric_cols[3].metric("Rejected", summary["rejected"])

    status_filter = st.selectbox("Status Filter", ["All", "Pending", "Approved", "Rejected"])
    urgency_values = ["All"] + sorted({str(case.get("urgency") or "UNKNOWN").upper() for case in cases if case.get("urgency")})
    urgency_filter = st.selectbox("Urgency Filter", urgency_values)

    filtered_cases = filter_cases(cases, status_filter=status_filter, urgency_filter=urgency_filter)
    if not filtered_cases:
        st.info("No cases match the selected filters.")
        return

    st.subheader("Cases")
    st.dataframe(build_table_rows(filtered_cases), use_container_width=True, hide_index=True)

    if st.button("Sync to Google Sheets", use_container_width=True):
        try:
            sync_all_to_google_sheets()
            st.success("Google Sheets sync completed successfully.")
        except ValueError as exc:
            st.error(f"Google Sheets configuration error: {exc}")
        except Exception as exc:  # pragma: no cover
            st.error(f"Google Sheets sync failed: {exc}")

    selected_number = st.selectbox(
        "Select covering number",
        [str(case.get("covering_number") or "") for case in filtered_cases],
        index=0,
    )

    case, participants, messages = _get_case_context(selected_number)
    if not case:
        st.warning("No details available for the selected covering number.")
        return

    st.subheader(f"Covering #{selected_number}")
    detail_columns = st.columns(3)
    detail_columns[0].write(f"**Covering number:** {case.get('covering_number')}")
    detail_columns[1].write(f"**Urgency:** {str(case.get('urgency') or 'UNKNOWN').upper()}")
    detail_columns[2].write(f"**Overall status:** {str(case.get('overall_status') or 'UNKNOWN').upper()}")
    st.write(f"**Description:** {case.get('description') or '—'}")
    st.write(f"**First seen:** {case.get('first_seen') or '—'}")
    st.write(f"**Last seen:** {case.get('last_seen') or '—'}")

    st.subheader("Participants")
    if participants:
        participant_rows = [
            {
                "Person": item.get("person") or "—",
                "Status": str(item.get("status") or "UNKNOWN").upper(),
                "Last Message": item.get("last_message") or "—",
            }
            for item in participants
        ]
        st.dataframe(participant_rows, use_container_width=True, hide_index=True)
    else:
        st.info("No participants recorded for this case.")

    st.subheader("Messages")
    if messages:
        message_rows = [
            {
                "Timestamp": item.get("timestamp") or "—",
                "Sender": item.get("sender") or "—",
                "Message": item.get("message") or "—",
            }
            for item in messages
        ]
        st.dataframe(message_rows, use_container_width=True, hide_index=True)
    else:
        st.info("No messages recorded for this case.")


if __name__ == "__main__":
    main()
