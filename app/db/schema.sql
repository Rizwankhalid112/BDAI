CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS profiles (
  id           SERIAL PRIMARY KEY,
  slug         TEXT UNIQUE NOT NULL,
  display_name TEXT NOT NULL,
  cv           JSONB NOT NULL,
  embedding    vector(1024),
  is_active    BOOLEAN NOT NULL DEFAULT TRUE,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS leads (
  id           SERIAL PRIMARY KEY,
  source       TEXT NOT NULL,
  source_url   TEXT,
  company      TEXT,
  title        TEXT,
  raw_text     TEXT NOT NULL,
  content_hash TEXT UNIQUE NOT NULL,
  parsed       JSONB,
  embedding    vector(1024),
  status       TEXT NOT NULL DEFAULT 'new'
               CHECK (status IN ('new','parsed','matched','needs_match_review',
                                 'tailored','in_review','approved','rejected','failed')),
  assigned_profile_id INT REFERENCES profiles(id),
  scraped_at   TIMESTAMPTZ,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS lead_status_history (
  id          SERIAL PRIMARY KEY,
  lead_id     INT NOT NULL REFERENCES leads(id),
  from_status TEXT,
  to_status   TEXT NOT NULL,
  changed_by  TEXT NOT NULL,
  note        TEXT,
  changed_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS matches (
  id             SERIAL PRIMARY KEY,
  lead_id        INT NOT NULL REFERENCES leads(id),
  profile_id     INT NOT NULL REFERENCES profiles(id),
  semantic       REAL NOT NULL,
  skill_overlap  REAL NOT NULL,
  exp_fit        REAL NOT NULL,
  total          REAL NOT NULL,
  matched_skills JSONB,
  missing_skills JSONB,
  is_chosen      BOOLEAN NOT NULL DEFAULT FALSE,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS generations (
  id                 SERIAL PRIMARY KEY,
  lead_id            INT NOT NULL REFERENCES leads(id),
  profile_id         INT NOT NULL REFERENCES profiles(id),
  llm_model          TEXT NOT NULL,
  prompt_version     TEXT NOT NULL,
  tailored_cv        JSONB,
  cover_letter       TEXT,
  gaps               JSONB,
  guardrail_flags    JSONB,
  ats_before         REAL,
  ats_after          REAL,
  final_cv           JSONB,
  final_cover_letter TEXT,
  status             TEXT NOT NULL DEFAULT 'draft'
                     CHECK (status IN ('draft','approved','rejected')),
  reviewer           TEXT,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  approved_at        TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS jobs (
  id          SERIAL PRIMARY KEY,
  job_type    TEXT NOT NULL CHECK (job_type IN ('parse','match','tailor','cover_letter','embed_profile')),
  payload     JSONB NOT NULL,
  status      TEXT NOT NULL DEFAULT 'queued'
              CHECK (status IN ('queued','running','done','failed')),
  attempts    INT NOT NULL DEFAULT 0,
  error       TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  started_at  TIMESTAMPTZ,
  finished_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs (status, created_at);
CREATE INDEX IF NOT EXISTS idx_leads_status ON leads (status);
CREATE INDEX IF NOT EXISTS idx_history_lead ON lead_status_history (lead_id);
CREATE INDEX IF NOT EXISTS idx_matches_lead ON matches (lead_id);
