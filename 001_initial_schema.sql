-- Run this in Supabase SQL Editor (Dashboard → SQL Editor → New query)

-- book_requests: one row per book job
CREATE TABLE IF NOT EXISTS book_requests (
  id                         BIGSERIAL PRIMARY KEY,
  title                      TEXT NOT NULL,
  notes_on_outline_before    TEXT,
  outline                    JSONB,
  notes_on_outline_after     TEXT,
  status_outline_notes       TEXT,          -- 'yes' | 'no' | 'no_notes_needed'
  chapter_notes_status       TEXT DEFAULT 'no_notes_needed',  -- global default for all chapters
  final_review_notes_status  TEXT,          -- 'yes' | 'no' | 'no_notes_needed'
  book_output_status         TEXT DEFAULT 'pending',
  -- book_output_status values:
  --   pending | outline_generated | waiting_outline_notes
  --   chapters_in_progress | waiting_final_notes | compiling | ready
  --   paused | error
  editor_email               TEXT,          -- optional: per-book notification recipient
  created_at                 TIMESTAMP WITH TIME ZONE DEFAULT now(),
  updated_at                 TIMESTAMP WITH TIME ZONE DEFAULT now()
);

-- chapters: one row per chapter per book
CREATE TABLE IF NOT EXISTS chapters (
  id                  BIGSERIAL PRIMARY KEY,
  book_request_id     BIGINT REFERENCES book_requests(id) ON DELETE CASCADE,
  chapter_number      INT NOT NULL,
  chapter_title       TEXT,
  chapter_content     TEXT,
  chapter_notes_status TEXT,               -- overrides global if set; 'yes'|'no'|'no_notes_needed'
  chapter_notes       TEXT,
  chapter_summary     TEXT,
  status              TEXT DEFAULT 'pending',
  -- status values: pending | needs_review | ready
  created_at          TIMESTAMP WITH TIME ZONE DEFAULT now(),
  updated_at          TIMESTAMP WITH TIME ZONE DEFAULT now(),
  UNIQUE (book_request_id, chapter_number)
);

-- notifications: log of every email/event sent
CREATE TABLE IF NOT EXISTS notifications (
  id               BIGSERIAL PRIMARY KEY,
  book_request_id  BIGINT REFERENCES book_requests(id) ON DELETE CASCADE,
  event_type       TEXT,
  details          JSONB,
  sent_at          TIMESTAMP WITH TIME ZONE DEFAULT now()
);

-- audit_logs: append-only pipeline event log
CREATE TABLE IF NOT EXISTS audit_logs (
  id               BIGSERIAL PRIMARY KEY,
  book_request_id  BIGINT REFERENCES book_requests(id) ON DELETE CASCADE,
  action           TEXT,
  payload          JSONB,
  created_at       TIMESTAMP WITH TIME ZONE DEFAULT now()
);

-- auto-update updated_at on book_requests
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER book_requests_updated_at
  BEFORE UPDATE ON book_requests
  FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER chapters_updated_at
  BEFORE UPDATE ON chapters
  FOR EACH ROW EXECUTE FUNCTION update_updated_at();
