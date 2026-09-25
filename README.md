# BD Lead-to-CV Tool

Internal tool for Aqua's BD team. Paste a job post; the tool picks the best-fit consultant profile, tailors that person's CV to the post without inventing anything, writes a short cover letter, and a person reviews and approves the result. All AI runs locally (Ollama in Docker); nothing leaves the machine.

How the pipeline works, and fake data to test with: **[FLOW.md](FLOW.md)**.

Only fake profiles may be used until GRC approves real CVs.

## 1. What to download

| What | Version | Why |
|---|---|---|
| Docker Engine (Linux) or Docker Desktop (Windows/macOS), with Compose v2 | recent | runs PostgreSQL + pgvector and Ollama |
| Python | 3.11 or newer | runs the app |
| Git | any | to get the code |

AI models (downloaded later with one command, about 6.5 GB, one time):

| Model | Size | Used for |
|---|---|---|
| `qwen3:8b` | 5.2 GB | reading job posts, tailoring the CV, writing the cover letter |
| `bge-m3` | 1.2 GB | embeddings for matching |

Machine: 16 GB RAM or more (the Ollama container is capped at 12 GB and 6 CPUs; both configurable in `.env`), about 10 GB free disk, no GPU needed. Ports used, all bound to localhost only: 5434 (PostgreSQL), 11434 (Ollama), 8501 (UI).

## 2. Setup (once)

Run all commands from the project folder.

```bash
# 1. Get the code
git clone <repository-url>
cd "BD AI"
git checkout phase-1-setup

# 2. Python environment
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 3. Settings
cp .env.example .env               # Windows: copy .env.example .env
```

Open `.env` and change two values:
- `POSTGRES_PASSWORD` — any password of your choice
- `LLM_TIMEOUT_SECONDS=600` — the example file says 300; CV tailoring on CPU can take longer than that

```bash
# 4. Start the database and Ollama
docker compose up -d
docker compose ps                  # db should show "healthy"

# 5. Download the AI models into the Ollama container (about 6.5 GB)
docker compose exec ollama ollama pull qwen3:8b
docker compose exec ollama ollama pull bge-m3
docker compose exec ollama ollama list

# 6. Create the database tables
python -m app.db.init_db

# 7. Check that everything works
python -m scripts.check_setup
```

Step 7 should print four `[OK]` lines: database tables, sample profiles, an LLM reply (the first one takes up to a minute while the model loads) and `bge-m3 embedding length: 1024`.

## 3. Run

Linux / macOS, one command (starts Docker services, the background worker and the UI; Ctrl+C stops all):

```bash
./scripts/dev.sh
```

Windows, or if you prefer two terminals:

```bash
python -m app.worker.worker                 # terminal 1: processes the queue
streamlit run ui/streamlit_app.py           # terminal 2: the UI
```

Open **http://127.0.0.1:8501**. The worker must be running, otherwise leads stay in the queue (the sidebar warns about this).

## 4. Test it (about 30 minutes, mostly waiting for the CPU)

1. **Profiles** page → *Load sample profiles* (two fake consultants). Optionally paste the third profile from [FLOW.md §7.2](FLOW.md#72-a-third-profile-to-paste-profiles--paste--upload-json). The "embedded" column turns ✅ within seconds.
2. **Add leads** page → select 2–3 sample posts (the pre-selected ones are a good start) and click *Add selected sample leads*, or paste the post from [FLOW.md §7.3](FLOW.md#73-a-job-post-to-paste-add-leads--paste-a-job-post).
3. **Leads & results** page → open a lead. It moves through Parse → Match → Tailor → Cover letter on its own (about 8 minutes per lead; the page refreshes itself). Then check:
   - *Match*: scores for every profile and the matched/missing skills. Expected winners are listed in [FLOW.md §7.4](FLOW.md#74-sample-job-posts-in-the-repository-and-the-expected-outcome). A lead that stops at *needs_match_review* is a deliberate close-call or no-fit case: pick a profile and click *Assign & tailor*.
   - *Tailored CV & ATS*: ATS before → after, the gaps list, guardrail flags, and the original and tailored CV side by side. Companies, roles and dates must be identical on both sides; nothing from the gaps list may appear in the tailored CV.
   - *Cover letter & review*: edit the text if you like, then *Approve*. The DRAFT banner disappears and *History* shows who approved it and when. Try *Regenerate* on another lead for a new draft.
4. **Jobs** page → queue and timing of every job. To see error handling: `docker stop bdtool-ollama`, add a lead, watch it turn *failed* with the error message, then `docker start bdtool-ollama` and click *Retry*.

## 5. Unit tests

```bash
pytest
```

53 tests, under a second, no AI needed (Ollama is mocked). They cover config loading, the Ollama client (timeouts, JSON retry), skill normalisation, matching maths, the ATS score, the cover-letter checks, and the guardrails, including deliberately planted invented skills and moved technologies.

## 6. Troubleshooting

| Problem | Fix |
|---|---|
| `port is already allocated` when starting Docker | another program uses that port; change `POSTGRES_PORT` in `.env` and run `docker compose up -d` again |
| `Could not reach Ollama` | `docker compose up -d`, then `docker compose logs ollama` |
| `model not found` | run the two `ollama pull` commands from step 5 |
| `Missing tables` | `python -m app.db.init_db` |
| a step fails with `timed out` | set `LLM_TIMEOUT_SECONDS=600` in `.env`, restart the worker, click *Retry* on the Jobs page; or use the faster `LLM_MODEL=qwen3:4b` (pull it first) |
| leads stay queued, sidebar says nothing is running | start the worker: `python -m app.worker.worker` |
| changed `.env` or a prompt file but nothing changed | the worker reads settings and prompts at startup: restart it |
| changed the database password after the first start | the database keeps the old one; `docker compose down -v && docker compose up -d` (deletes all data) |

Useful commands:

```bash
docker compose stop                                        # stop containers, keep data
docker compose exec db psql -U bdtool -d bdtool -c '\dt'   # list tables
docker compose exec db psql -U bdtool -d bdtool            # SQL prompt
```

## 7. Project layout

```
app/config.py            settings loaded from .env (refuses non-local AI addresses)
app/db/schema.sql        the six tables
app/db/repo.py           all SQL, including the one function that changes a lead's status and logs it
app/llm/client.py        the only code that calls the AI: chat_text, chat_json, embed
app/llm/prompts/         versioned prompts: parse, tailor, cover_letter
app/models/              Pydantic models: profile, parsed lead, match score, tailored CV
app/pipeline/            skills, parse, match, tailor, guardrails, ats, cover_letter
app/worker/worker.py     background worker that runs the queue
app/services.py          the actions the UI buttons call
ui/streamlit_app.py      the web UI
data/samples/            2 fake profiles, 10 fake job posts
eval/test_set.json       expected profile for each sample post
scripts/check_setup.py   "is everything working?" check
scripts/dev.sh           starts everything for local testing
tests/                   unit tests
FLOW.md                  how the pipeline works + fake test data
```

## 8. Not built yet

DOCX/PDF export, automatic collection of job posts from job sites (needs GRC approval of the sites), a comparison of `qwen3:4b` vs `qwen3:8b`, and the server deployment with resource limits.
