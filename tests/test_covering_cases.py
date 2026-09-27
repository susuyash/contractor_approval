from covering_cases import extract_covering_cases


def test_extracts_covering_number_from_explicit_format():
    messages = [
        {
            "timestamp": "2024-01-02T09:00:00",
            "sender": "Jay",
            "text": "COVERING NO_259.pdf",
        },
        {
            "timestamp": "2024-01-02T09:05:00",
            "sender": "Mehta",
            "text": "259 approved",
        },
    ]

    cases = extract_covering_cases(messages)

    assert cases == [
        {
            "covering_number": "259",
            "messages": messages,
            "participants": ["Jay", "Mehta"],
            "first_seen": "2024-01-02T09:00:00",
            "last_seen": "2024-01-02T09:05:00",
        }
    ]


def test_groups_messages_referring_to_existing_covering_without_false_positive_matches():
    messages = [
        {
            "timestamp": "2024-01-02T09:00:00",
            "sender": "Jay",
            "text": "Covering number 259",
        },
        {
            "timestamp": "2024-01-02T09:10:00",
            "sender": "Rishabh",
            "text": "Need update for 259",
        },
        {
            "timestamp": "2024-01-02T09:12:00",
            "sender": "Alice",
            "text": "Budget is 1500 for Jan 2024",
        },
    ]

    cases = extract_covering_cases(messages)

    assert cases == [
        {
            "covering_number": "259",
            "messages": messages[:2],
            "participants": ["Jay", "Rishabh"],
            "first_seen": "2024-01-02T09:00:00",
            "last_seen": "2024-01-02T09:10:00",
        }
    ]
