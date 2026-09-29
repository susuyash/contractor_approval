CREATE TABLE IF NOT EXISTS cases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    covering_number TEXT NOT NULL UNIQUE,
    description TEXT,
    urgency TEXT,
    raised_by TEXT,
    overall_status TEXT NOT NULL CHECK (overall_status IN ('PENDING', 'APPROVED', 'REJECTED', 'UNKNOWN')),
    first_seen TIMESTAMPTZ,
    last_seen TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS participants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id UUID NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    person TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('OK', 'APPROVED', 'PENDING', 'REJECTED', 'UNKNOWN')),
    last_message TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (case_id, person)
);

CREATE TABLE IF NOT EXISTS messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id UUID NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    sender TEXT NOT NULL,
    message TEXT NOT NULL,
    timestamp TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (case_id, sender, message, timestamp)
);

CREATE TABLE IF NOT EXISTS whapi_inbox (
    event_key TEXT PRIMARY KEY,
    payload JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'processing', 'sheets_pending', 'complete')),
    attempts INTEGER NOT NULL DEFAULT 0,
    locked_at TIMESTAMPTZ,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cases_covering_number ON cases(covering_number);
CREATE INDEX IF NOT EXISTS idx_cases_overall_status ON cases(overall_status);
CREATE INDEX IF NOT EXISTS idx_participants_case_id ON participants(case_id);
CREATE INDEX IF NOT EXISTS idx_messages_case_id ON messages(case_id);
CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp);
CREATE INDEX IF NOT EXISTS idx_whapi_inbox_status_retry ON whapi_inbox(status, next_attempt_at, created_at);

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS cases_updated_at_trigger ON cases;
CREATE TRIGGER cases_updated_at_trigger
BEFORE UPDATE ON cases
FOR EACH ROW
EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS participants_updated_at_trigger ON participants;
CREATE TRIGGER participants_updated_at_trigger
BEFORE UPDATE ON participants
FOR EACH ROW
EXECUTE FUNCTION update_updated_at_column();
DROP TRIGGER IF EXISTS whapi_inbox_updated_at_trigger ON whapi_inbox;
CREATE TRIGGER whapi_inbox_updated_at_trigger
BEFORE UPDATE ON whapi_inbox
FOR EACH ROW
EXECUTE FUNCTION update_updated_at_column();
ALTER TABLE cases ENABLE ROW LEVEL SECURITY;
ALTER TABLE participants ENABLE ROW LEVEL SECURITY;
ALTER TABLE messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE whapi_inbox ENABLE ROW LEVEL SECURITY;
