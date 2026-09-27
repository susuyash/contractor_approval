from app import filter_cases, summarize_cases


def test_summarize_cases_counts_statuses():
    cases = [
        {"overall_status": "APPROVED"},
        {"overall_status": "PENDING"},
        {"overall_status": "REJECTED"},
        {"overall_status": "APPROVED"},
    ]

    summary = summarize_cases(cases)

    assert summary == {"total": 4, "pending": 1, "approved": 2, "rejected": 1}


def test_filter_cases_applies_status_and_urgency_filters():
    cases = [
        {"covering_number": "259", "overall_status": "APPROVED", "urgency": "URGENT", "updated_at": "2026-08-22T00:00:00Z"},
        {"covering_number": "260", "overall_status": "PENDING", "urgency": "NORMAL", "updated_at": "2026-08-21T00:00:00Z"},
        {"covering_number": "261", "overall_status": "APPROVED", "urgency": "NORMAL", "updated_at": "2026-08-20T00:00:00Z"},
    ]

    filtered = filter_cases(cases, status_filter="Approved", urgency_filter="Urgent")

    assert [item["covering_number"] for item in filtered] == ["259"]

    filtered_all = filter_cases(cases, status_filter="All", urgency_filter="Normal")
    assert [item["covering_number"] for item in filtered_all] == ["260", "261"]
