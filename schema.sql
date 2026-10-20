-- AI Resume Parser — Supabase Schema
CREATE TABLE IF NOT EXISTS resumes (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id UUID NOT NULL,
  file_name TEXT,
  storage_path TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'uploaded' CHECK (status IN ('uploaded', 'parsed', 'failed')),
  parsed_data JSONB,
  parse_confidence REAL,
  error_message TEXT,
  parsed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Usage credits (free tier: 3 parses free, then Paymob/Wise)
CREATE TABLE IF NOT EXISTS user_credits (
  user_id UUID PRIMARY KEY,
  free_parses_remaining INT DEFAULT 3,
  paid_credits INT DEFAULT 0,
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_resumes_status ON resumes(status);
CREATE INDEX IF NOT EXISTS idx_resumes_user ON resumes(user_id);

ALTER TABLE resumes ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_credits ENABLE ROW LEVEL SECURITY;
