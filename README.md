# Contractor RFI Payment Tracker

This repository contains the first milestone for the contractor payment approval tracker: a WhatsApp export parser.

## What this milestone does

- Parses a raw WhatsApp TXT export into structured message records
- Extracts:
  - timestamp
  - sender
  - message text
- Handles ordinary WhatsApp formatting and multiline messages
- Uses no LLM or external service

## Project layout

- `parser.py` - WhatsApp export parser
- `tests/test_parser.py` - automated parser tests

## Setup

1. Create and activate a virtual environment (optional but recommended):

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   ```

   On Windows PowerShell:

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

2. Install requirements:

   ```bash
   pip install -r requirements.txt
   ```

## Run the tests

```bash
pytest -q
```

## Example usage

```python
from parser import parse_whatsapp_export

raw = """12/31/2023, 9:15 AM - Alice: Hello there
12/31/2023, 9:16 AM - Bob: Payment approved
Need to confirm the invoice."""

messages = parse_whatsapp_export(raw)
print(messages)
```

## Notes

This milestone intentionally excludes the live WhatsApp ingestion layer, database, dashboards, and AI components. Those will be added in later milestones.
