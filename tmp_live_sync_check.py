import os

from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build

from database import get_cases, get_participants, get_messages
from google_sheets import sync_all_to_google_sheets

load_dotenv()

spreadsheet_id = os.getenv('GOOGLE_SHEETS_SPREADSHEET_ID')
creds_path = os.getenv('GOOGLE_SERVICE_ACCOUNT_CREDENTIALS_PATH')

if not spreadsheet_id:
    raise RuntimeError('Missing GOOGLE_SHEETS_SPREADSHEET_ID')
if not creds_path:
    raise RuntimeError('Missing GOOGLE_SERVICE_ACCOUNT_CREDENTIALS_PATH')

cases = get_cases()
participants = []
messages = []
for case in cases:
    case_id = case.get('id')
    participants.extend(get_participants(case_id=case_id))
    messages.extend(get_messages(case_id=case_id))

print('CASE_COUNT', len(cases))
print('PARTICIPANT_COUNT', len(participants))
print('MESSAGE_COUNT', len(messages))

sync_all_to_google_sheets()

creds = service_account.Credentials.from_service_account_file(
    creds_path,
    scopes=['https://www.googleapis.com/auth/spreadsheets'],
)
service = build('sheets', 'v4', credentials=creds)
spreadsheet = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
print('SPREADSHEET_TITLE', spreadsheet.get('properties', {}).get('title'))
print('WORKSHEET_NAMES', [sheet['properties']['title'] for sheet in spreadsheet.get('sheets', [])])

for name in ['Cases', 'Participants', 'Messages']:
    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=name,
    ).execute()
    values = result.get('values', [])
    row_count = max(len(values) - 1, 0)
    print(f'{name}_ROWS', row_count)
    if name == 'Cases':
        has_259 = any(str(row[0]).strip() == '259' for row in values[1:] if row)
        print('HAS_COVERING_259', has_259)
