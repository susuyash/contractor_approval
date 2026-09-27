from pathlib import Path
import json

from parser import parse_whatsapp_export
from covering_cases import extract_covering_cases
from ai_processor import interpret_covering_case
from database import get_supabase_client, save_case

export_candidates = [
    Path(r'E:\PROJECTS\contractor_rfi\whatsapp_export.txt'),
    Path(r'E:\PROJECTS\contractor_rfi\whatsapp_export.txt.txt'),
]

export_path = next((p for p in export_candidates if p.exists()), None)
if export_path is None:
    raise FileNotFoundError('No WhatsApp export file found in project root.')

raw = export_path.read_text(encoding='utf-8')
messages = parse_whatsapp_export(raw)
cases = extract_covering_cases(messages)

if not cases:
    raise RuntimeError('No covering cases found in the real WhatsApp export.')

client = get_supabase_client()
existing = client.table('cases').select('covering_number').execute()
existing_numbers = {str(item['covering_number']) for item in (existing.data or [])}

selected = next((case for case in cases if str(case['covering_number']) not in existing_numbers), None)
if selected is None:
    raise RuntimeError('No unprocessed real covering case was available for the live integration test.')

selected_number = str(selected['covering_number'])
print('SELECTED_COVERING_NUMBER', selected_number)
print('TOTAL_CASES_DETECTED', len(cases))

interpretation = interpret_covering_case(selected)
print('GEMINI_RESULT', json.dumps({
    'covering_number': interpretation.covering_number,
    'description': interpretation.description,
    'urgency': interpretation.urgency.value,
    'overall_status': interpretation.overall_status.value,
    'participants': [p.model_dump() for p in interpretation.participants],
}, default=str))

payload = {
    'covering_number': selected_number,
    'description': interpretation.description,
    'urgency': interpretation.urgency.value if interpretation.urgency else None,
    'overall_status': interpretation.overall_status.value if interpretation.overall_status else None,
    'first_seen': selected['first_seen'],
    'last_seen': selected['last_seen'],
    'participants': [p.model_dump() for p in interpretation.participants],
    'messages': selected['messages'],
}

saved = save_case(client, payload, interpretation)
print('SAVE_RESULT', json.dumps({
    'id': saved.get('id'),
    'covering_number': saved.get('covering_number'),
    'overall_status': saved.get('overall_status'),
}, default=str))

rows = client.table('cases').select('*').eq('covering_number', selected_number).execute().data or []
case_id = rows[0]['id'] if rows else None
participant_rows = client.table('participants').select('*').eq('case_id', case_id).execute().data if case_id else []
message_rows = client.table('messages').select('*').eq('case_id', case_id).execute().data if case_id else []
print('CASE_ROWS_COUNT', len(rows))
print('PARTICIPANTS_SAVED', len(participant_rows))
print('MESSAGES_SAVED', len(message_rows))
print('CASE_RETRIEVED_OK', bool(rows and rows[0].get('covering_number') == selected_number))

saved_again = save_case(client, payload, interpretation)
rows_after = client.table('cases').select('*').eq('covering_number', selected_number).execute().data or []
print('CASE_ROWS_AFTER_SECOND_SAVE', len(rows_after))
print('DUPLICATE_TEST_RESULT', 'NO_DUPLICATE' if len(rows_after) == len(rows) else 'DUPLICATE_FOUND')
print('SECOND_SAVE_CASE_ID', saved_again.get('id'))
