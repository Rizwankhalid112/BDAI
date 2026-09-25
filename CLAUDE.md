# CLAUDE.md — BD Lead-to-CV Tool

This file is the development spec for this project. Read it fully before writing any code. Work **one phase at a time** (see "Build Phases"), and stop at the end of each phase so the developer can review, test, and approve before you continue.

The developer is new to AI development and is learning Python and PostgreSQL. Explain what you are doing in plain language as you go, keep the code simple and well-commented, and prefer clarity over cleverness.

---

## 1. What we are building

An internal tool for Aqua's BD team.

1. Job posts ("leads") are collected from job platforms (scraped) or pasted in manually.
2. Each lead is compared against the consultant profiles stored in the system (currently two people) and assigned to the best-fit profile.
3. The assigned person's stored CV is tailored to the lead: ATS-friendly, using the lead's keywords, **without inventing anything**.
4. A short cover letter is generated.
5. A BD team member reviews, edits, and approves. Only then are the CV and cover letter exported (DOCX + PDF).

Scope is deliberately small. Do not add features beyond this workflow unless asked.

---

## 2. Hard rules (never break these)

1. **No external AI services.** The only AI calls allowed are to the local Ollama API (`http://localhost:11434`). No OpenAI, Anthropic, Hugging Face Inference, or any other hosted API, and no library that silently calls one.
2. **Dummy data only during development.** Use the fake profiles in `data/samples/`. Real employee CVs are loaded only after GRC approval — never commit real CVs, and never put real personal data in code, tests, logs, or file names.
3. **No secrets in code.** All config comes from `.env` (listed in `.gitignore`). Provide `.env.example` with placeholder values only.
4. **Never invent experience.** The tailoring step may rephrase, reorder, and emphasize what already exists in a profile. It must never add skills, tools, employers, titles, dates, degrees, or certifications that are not in the profile. This is enforced by code (see §7.4), not just by the prompt.
5. **Everything generated is a draft** until a human approves it. Exported files must be clearly marked as drafts until status is `approved`.
6. **Git hygiene.** Work on feature branches (`phase-1-setup`, etc.). Never push to `main` or other protected branches directly. All code goes through human review and the standard security gates.
7. **Log every status change** of a lead in `lead_status_history` from day 1.
8. **Don't mask failures.** If the LLM returns invalid output after a retry, mark the job as failed with the error, and show it in the UI. Never silently fill in defaults.

---

## 3. Environment

- **Development machine:** developer's laptop, 32 GB RAM, **no GPU** (CPU-only inference). OS: **Linux** (confirmed). Note: host port 5432 is already used by a local Postgres, so the project database is exposed on **5434** (5433 is used by another project).
- **Production target (later):** a CPU-only on-prem server that also runs the company HRMS. The AI must run in Docker with CPU and RAM limits so it can never starve HRMS. Installation there requires IT/GRC sign-off (not a coding task).

---

## 4. Tech stack

| Part | Choice | Notes |
|---|---|---|
| Language | Python 3.11+ | Type hints everywhere |
| UI | Streamlit | Calls the service layer directly (no separate API server in MVP) |
| Database | PostgreSQL 16 + pgvector | Run via Docker image `pgvector/pgvector:pg16` (easiest on every OS) |
| DB access | psycopg 3 | Plain SQL, no heavy ORM. Schema in `app/db/schema.sql` |
| Validation | Pydantic v2 | All LLM JSON output is validated against Pydantic models |
| LLM runtime | Ollama | Local only |
| LLM model | `qwen3:8b` (dev), `qwen3:4b` fallback | Model name comes from config; switching must be a one-line change |
| Embeddings | `bge-m3` via Ollama | 1024-dim vectors |
| Scraping | Playwright + BeautifulSoup | Phase 5 only |
| CV export | docxtpl (Word template) → LibreOffice headless for PDF | `soffice --headless --convert-to pdf` |
| Job queue | Postgres `jobs` table + one Python worker | `SELECT ... FOR UPDATE SKIP LOCKED`, one job at a time |
| Tests | pytest | LLM calls mocked in unit tests |
| Deployment | Docker Compose with CPU/RAM limits | Phase 6 |

### LLM settings
- Parsing: `temperature 0.1`. Writing (tailor, cover letter): `temperature 0.4`.
- `num_ctx: 8192`.
- Use Ollama's structured output (`format` with a JSON schema generated from the Pydantic model) for every JSON-returning call.
- Qwen3 has a "thinking" mode that is slow on CPU. Disable it for these tasks (Ollama `think: false`; verify the current parameter name in the Ollama docs).
- Timeouts: 300 s per LLM call (CPU is slow). Retry once on invalid output, then fail the job.

---

## 5. Project structure

```
cv-bd-tool/
├── CLAUDE.md
├── README.md                 # setup + run instructions for humans
├── .env.example
├── .gitignore                # includes .env, outputs/, __pycache__, real data
├── requirements.txt
├── docker-compose.yml        # Phase 1: postgres only. Phase 6: full stack with limits
├── app/
│   ├── config.py             # loads .env: DB URL, OLLAMA_URL, LLM_MODEL, EMBED_MODEL, thresholds
│   ├── db/
│   │   ├── schema.sql
│   │   ├── connection.py
│   │   └── repo.py           # all SQL queries live here
│   ├── models/               # Pydantic models: profile, lead, match, generation
│   ├── llm/
│   │   ├── client.py         # Ollama chat + embed wrapper (timeouts, retry, JSON validation)
│   │   └── prompts/          # parse.txt, tailor.txt, cover_letter.txt
│   ├── pipeline/
│   │   ├── skills.py         # skill normalization + synonym map
│   │   ├── parse.py
│   │   ├── match.py
│   │   ├── tailor.py
│   │   ├── guardrails.py
│   │   ├── ats.py
│   │   └── cover_letter.py
│   ├── worker/
│   │   └── worker.py         # polls jobs table, runs pipeline steps
│   ├── export/
│   │   ├── templates/cv_template.docx
│   │   ├── templates/cover_letter_template.docx
│   │   └── render.py
│   └── scrapers/             # Phase 5
│       ├── base.py
│       └── <platform>.py
├── ui/
│   └── streamlit_app.py
├── data/
│   └── samples/
│       ├── profiles/         # 2 fake profiles as JSON
│       └── leads/            # ~10 sample job posts as .txt
├── eval/
│   ├── test_set.json         # leads with the expected correct profile
│   └── run_eval.py
└── tests/
```

---

## 6. Data model

### 6.1 Profile CV (stored as JSONB, validated by Pydantic)

```json
{
  "name": "Sample Person A",
  "headline": "Senior Backend Engineer",
  "total_years_experience": 7,
  "summary": "...",
  "skills": ["Python", "PostgreSQL", "AWS", "Docker"],
  "experience": [
    {
      "company": "Example Co",
      "role": "Backend Engineer",
      "start": "2021-03",
      "end": "present",
      "bullets": ["Built ...", "Reduced ..."]
    }
  ],
  "education": [{"degree": "BS Computer Science", "institution": "...", "year": "2017"}],
  "certifications": ["..."],
  "contact": {"email": "person.a@example.com", "phone": "+00 000 0000000", "location": "..."}
}
```

### 6.2 Tables (`app/db/schema.sql`)

See `app/db/schema.sql` for the authoritative version (tables: `profiles`, `leads`, `lead_status_history`, `matches`, `generations`, `jobs`).

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE profiles (
  id           SERIAL PRIMARY KEY,
  slug         TEXT UNIQUE NOT NULL,
  display_name TEXT NOT NULL,
  cv           JSONB NOT NULL,
  embedding    vector(1024),
  is_active    BOOLEAN NOT NULL DEFAULT TRUE,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE leads (
  id           SERIAL PRIMARY KEY,
  source       TEXT NOT NULL,              -- 'manual' or platform name
  source_url   TEXT,
  company      TEXT,
  title        TEXT,
  raw_text     TEXT NOT NULL,
  content_hash TEXT UNIQUE NOT NULL,       -- sha256 of normalized raw_text, for dedupe
  parsed       JSONB,
  embedding    vector(1024),
  status       TEXT NOT NULL DEFAULT 'new'
               CHECK (status IN ('new','parsed','matched','needs_match_review',
                                 'tailored','in_review','approved','rejected','failed')),
  assigned_profile_id INT REFERENCES profiles(id),
  scraped_at   TIMESTAMPTZ,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE lead_status_history (
  id         SERIAL PRIMARY KEY,
  lead_id    INT NOT NULL REFERENCES leads(id),
  from_status TEXT,
  to_status  TEXT NOT NULL,
  changed_by TEXT NOT NULL,                -- 'system' or reviewer name
  note       TEXT,
  changed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE matches (
  id            SERIAL PRIMARY KEY,
  lead_id       INT NOT NULL REFERENCES leads(id),
  profile_id    INT NOT NULL REFERENCES profiles(id),
  semantic      REAL NOT NULL,
  skill_overlap REAL NOT NULL,
  exp_fit       REAL NOT NULL,
  total         REAL NOT NULL,
  matched_skills JSONB,
  missing_skills JSONB,
  is_chosen     BOOLEAN NOT NULL DEFAULT FALSE,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE generations (
  id                 SERIAL PRIMARY KEY,
  lead_id            INT NOT NULL REFERENCES leads(id),
  profile_id         INT NOT NULL REFERENCES profiles(id),
  llm_model          TEXT NOT NULL,
  prompt_version     TEXT NOT NULL,
  tailored_cv        JSONB,
  cover_letter       TEXT,
  gaps               JSONB,                -- lead keywords the person does not have
  guardrail_flags    JSONB,
  ats_before         REAL,
  ats_after          REAL,
  final_cv           JSONB,                -- after human edits
  final_cover_letter TEXT,
  status             TEXT NOT NULL DEFAULT 'draft'
                     CHECK (status IN ('draft','approved','rejected')),
  reviewer           TEXT,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  approved_at        TIMESTAMPTZ
);

CREATE TABLE jobs (
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
```

Every change to `leads.status` goes through one function in `repo.py` that also writes `lead_status_history`.

---

## 7. Pipeline specification

The worker chains the steps: `parse → match → tailor → cover_letter`. Each step is its own job, so a failure in one step can be retried without redoing the others.

### 7.1 Parse

Input: `raw_text`. Output (Pydantic `ParsedLead`):

```json
{
  "title": "string",
  "company": "string or null",
  "seniority": "junior | mid | senior | lead | null",
  "years_experience_required": "number or null",
  "required_skills": ["..."],
  "nice_to_have_skills": ["..."],
  "tools": ["..."],
  "domain": "string or null",
  "location": "string or null",
  "remote": "true | false | null",
  "responsibilities": ["short phrases"]
}
```

Prompt rules: extract only what the post says. Use null or an empty list when something isn't mentioned. Never guess.

After parsing, normalize all skill names through `skills.py` (lowercase, trim, synonym map, e.g. `js→javascript`, `k8s→kubernetes`, `postgres→postgresql`, `node→node.js`). Keep the synonym map in a plain dict that is easy to extend.

### 7.2 Match (no chat LLM)

For each active profile:

- **semantic** = cosine similarity between the lead embedding and the profile embedding.
  - Lead embedding text: title + required skills + tools + responsibilities.
  - Profile embedding text: headline + summary + skills + all roles.
- **skill_overlap** = |required ∩ profile skills| / |required|. Profile skills include the skills list plus any skills found in the bullets. If there are no required skills, use `tools` instead; if that is also empty, use 0.5.
- **exp_fit** = 1.0 if no years are required or the profile meets them; otherwise `max(0, 1 − (required − have) / required)`.
- **total** = `0.50·semantic + 0.35·skill_overlap + 0.15·exp_fit`. The weights are in config.

Decision:
- The highest total wins.
- If `best − second_best < 0.05` or `best < 0.50` (both thresholds in config), set the status to `needs_match_review` and let a human choose in the UI.
- Save every profile's scores in `matches`, including matched and missing skills, so the UI can show why the choice was made.

### 7.3 Tailor

Input: the parsed lead plus the assigned profile CV. The LLM returns a `TailoredCV` with:

- `summary` (rewritten, 3–4 lines, using the lead's language where truthful)
- `skills` (the same set as the profile, reordered so the most relevant come first; may use the lead's wording for an equivalent skill, e.g. "PostgreSQL" vs "Postgres")
- `experience`: the same entries in the same order, with company, role, and dates **copied exactly**; only the bullets are rephrased or reordered, and the number of bullets per role stays the same or goes down
- education and certifications are copied unchanged by code, not by the LLM

The prompt must state the no-invention rule explicitly and include one short example of a good rewrite.

### 7.4 Guardrails (code, runs after tailoring)

1. Company, role, start, end, education, and certifications must be identical to the profile. If any differ, overwrite them with the profile values and add a flag.
2. Extract skill terms from the tailored summary, skills, and bullets. Any term that is a lead keyword but does not appear anywhere in the original profile is an **invented claim**: remove it or retry once with a stricter prompt; if it persists, flag it for the reviewer.
3. Every rewritten bullet is checked against the **same role's** original bullets (added 2026-09-25 after testing showed the model copying job responsibilities into the CV and moving Celery/Redis to a different employer): it must share at least 35% of its content words with one original bullet of that role, and it must not mention a technology (profile skill or lead keyword) that the role's original text never mentioned. Problems trigger the one stricter retry; anything that persists is **replaced by the original bullet** and flagged as `fixed`, so invented text never reaches the reviewer.
4. Lead keywords that the profile genuinely lacks are saved as `gaps`, shown to the reviewer, and never inserted.
5. Save all flags in `generations.guardrail_flags`.

### 7.5 ATS score (code)

- Keywords = required skills (weight 2) + nice-to-have skills + tools (weight 1), normalized.
- Match against the CV text case-insensitively on word boundaries, including synonyms.
- Score = matched weight / total weight × 100. Compute it for the original profile (`ats_before`) and the tailored CV (`ats_after`).

### 7.6 Cover letter

Input: the parsed lead plus the tailored CV. 3–4 short paragraphs, 250 words or fewer, professional tone, no invented facts, no clichés like "I am writing to express my interest." It should mention two or three concrete matching achievements from the CV. Return plain text.

### 7.7 Export

- Render `final_cv` (or `tailored_cv` if not yet edited) into `cv_template.docx` with docxtpl.
- The template must be ATS-friendly: a single column, standard headings (Summary, Skills, Experience, Education, Certifications), no tables, text boxes, images, or icons, standard fonts, and contact details in the body rather than the header or footer.
- Convert to PDF with LibreOffice headless.
- File names: `{profile_slug}_{company_slug}_{YYYYMMDD}_{status}.docx`. File names must never contain contact details.
- Until the generation is approved, add a visible "DRAFT — requires human review" line.

---

## 8. UI (Streamlit)

Pages:

1. **Add lead**: paste the job post text, plus optional company and URL. The page creates the lead and queues a parse job.
2. **Leads**: a table with title, company, source, status, and assigned profile, filterable by status.
3. **Lead detail**:
   - the raw post and the parsed JSON
   - match scores for every profile, with matched and missing skills
   - a manual override to choose a different profile (logged in history)
   - the tailored CV shown side by side with the original, differences highlighted
   - the gaps list, guardrail flags, and ATS before → after
   - an editable cover letter
   - Approve, Reject, and Regenerate buttons
   - DOCX and PDF downloads
4. **Profiles**: view profiles and upload or replace one from JSON (re-embeds automatically).
5. **Jobs**: queue status and failed jobs with their error messages.

Show a clear "processing… this can take a few minutes on CPU" state for any lead that has queued or running jobs.

---

## 9. Build phases

Stop after each phase and summarize what was built, how to run it, and how to test it.

**Phase 1: Setup**
- `docker-compose.yml` with Postgres + pgvector, `schema.sql`, `config.py`, `.env.example`, `requirements.txt`
- An Ollama client that can chat and embed (`qwen3:8b`, `bge-m3`)
- Two fake profiles and about 10 sample leads in `data/samples/`, covering different stacks so matching is testable
- A README with setup steps
- *Done when:* a script prints an LLM reply and an embedding length of 1024, and the database tables exist.

**Phase 2: Parse and match**
- `parse.py`, `skills.py`, `match.py`, the worker, profile embedding
- *Done when:* all sample leads parse into valid JSON and are assigned, and `eval/run_eval.py` reports match accuracy on the test set.

**Phase 3: Tailor, guardrails, ATS, cover letter**
- *Done when:* for every sample lead there is a tailored CV with no guardrail violations left unflagged, `ats_after ≥ ats_before`, and a cover letter. Unit tests cover the guardrails, including a deliberately planted invented skill.

**Phase 4: UI and export**
- The Streamlit pages from §8 and DOCX/PDF export
- *Done when:* a lead can go from paste to approved to download entirely through the UI.

**Phase 5: Scrapers**
- One platform at a time, behind a common `base.py` interface
- Candidate platforms: LinkedIn, Indeed, rozee.pk. **Confirm with the developer which ones are approved before writing any scraper**, because the platforms' terms of service need GRC review.
- Polite scraping: rate limits, random delays, respect robots.txt, stop on block rather than bypass it. Dedupe via `content_hash`.
- *Done when:* the approved platform scraper adds new leads that flow through the pipeline automatically.

**Phase 6: Evaluation and deployment prep**
- Expand the eval set and add a report comparing `qwen3:4b` and `qwen3:8b` (speed per step, match accuracy, ATS gain, guardrail flag rate)
- A full `docker-compose.yml` with CPU and memory limits (e.g. `cpus: "4"`, `mem_limit: 10g`, both configurable)
- *Done when:* the whole stack starts with one command, and the resource limits are verified.

---

## 10. Future (do not build now)

- A training-data export: approved (lead, draft, final) triples as JSONL, for possible LoRA fine-tuning later. The `generations` table already stores everything needed, so no extra work is required now.
- A FastAPI layer if a non-Streamlit frontend is ever needed.

---

## 11. Coding conventions

- Small functions, type hints, docstrings in plain English.
- All prompts live in `app/llm/prompts/` as text files with a version string, which is saved in `generations.prompt_version`.
- Unit tests mock Ollama. One optional integration test hits real Ollama, marked `@pytest.mark.slow`.
- Log with the standard `logging` module. Never log full CV contents or contact details, only IDs.
- When unsure about a requirement, ask the developer instead of guessing.
