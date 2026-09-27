from parser import parse_whatsapp_export


def test_parses_single_message():
    raw = "12/31/2023, 9:15 AM - Alice: Hello there"

    messages = parse_whatsapp_export(raw)

    assert messages == [
        {
            "timestamp": "2023-12-31T09:15:00",
            "sender": "Alice",
            "text": "Hello there",
        }
    ]


def test_parses_multiline_message_and_multiple_messages():
    raw = (
        "12/31/2023, 9:15 AM - Alice: Hello there\n"
        "12/31/2023, 9:16 AM - Bob: Payment approved\n"
        "We need to confirm the invoice.\n"
        "Please send the final PDF.\n"
        "12/31/2023, 9:18 AM - Alice: Received"
    )

    messages = parse_whatsapp_export(raw)

    assert messages == [
        {
            "timestamp": "2023-12-31T09:15:00",
            "sender": "Alice",
            "text": "Hello there",
        },
        {
            "timestamp": "2023-12-31T09:16:00",
            "sender": "Bob",
            "text": "Payment approved\nWe need to confirm the invoice.\nPlease send the final PDF.",
        },
        {
            "timestamp": "2023-12-31T09:18:00",
            "sender": "Alice",
            "text": "Received",
        },
    ]


def test_handles_day_first_export_and_metadata_lines():
    raw = (
        "Messages and calls are end-to-end encrypted.\n"
        "31/12/2023, 09:20 - Charlie: Invoice uploaded\n"
        "31/12/2023, 09:22 - Dana: That looks good\n"
        "Please review the amount."
    )

    messages = parse_whatsapp_export(raw)

    assert messages == [
        {
            "timestamp": "2023-12-31T09:20:00",
            "sender": "Charlie",
            "text": "Invoice uploaded",
        },
        {
            "timestamp": "2023-12-31T09:22:00",
            "sender": "Dana",
            "text": "That looks good\nPlease review the amount.",
        },
    ]
