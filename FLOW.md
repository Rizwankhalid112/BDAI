# BD Lead-to-CV Tool — how the flow works

This document explains what happens from the moment a job post enters the system until a reviewer approves a tailored CV and cover letter. Setup and run instructions are in [README.md](README.md). Section 7 contains fake test data you can paste in.

## 1. Big picture

```
Consultant profile (JSON) ──► stored + embedded once
                                          │
Job post ("lead") ──► parse ──► match ──┴─► tailor + guardrails + ATS score ──► cover letter ──► human review ──► approved
```

- All AI runs on a **local Ollama server** in Docker. Nothing is sent to any external AI service.
- Two models: **qwen3:8b** reads the job post, rewrites the CV and writes the letter; **bge-m3** turns text into 1024-number vectors used for matching.
- Everything the AI produces is a **draft** until a person approves it in the UI.
- The tool may reword and reorder what is already in a profile. It must never add skills, employers, titles, dates, degrees or achievements. This is enforced by code (section 3, step 4), not only by the prompt.

## 2. The two moving parts

| Part | File | What it does |
|---|---|---|
| **UI** (Streamlit) | `ui/streamlit_app.py` | Pages: Profiles, Add leads, Leads & results, Jobs. Writes to the database and puts jobs in a queue. Never calls the AI itself. |
| **Worker** | `app/worker/worker.py` | Polls the `jobs` table every 2 s, runs **one job at a time**, and queues the next step when a step succeeds. |

Why a queue: on a CPU-only machine one AI call takes 1–5 minutes, so the UI would freeze if it called the AI directly. Instead the UI shows "processing…" and refreshes itself.

Job types: `embed_profile`, `parse`, `match`, `tailor`, `cover_letter`. Each job records its attempts and, if it fails, the error message. A failed step marks the lead `failed` and shows the error on the Jobs page, where **Retry** re-queues it. Nothing is silently replaced with defaults.

All SQL lives in `app/db/repo.py`. Every change of a lead's status goes through one function there, which also writes a row to `lead_status_history` (who, when, why).

## 3. Step by step

### Step 0 — Profiles

- A profile is a JSON document (name, headline, years, summary, skills, experience with bullets, education, certifications, contact). It is validated by `app/models/profile.py`; wrong or missing fields are rejected with a clear message.
- Saving a profile queues an `embed_profile` job: bge-m3 embeds *headline + summary + skills + all role titles* and the vector is stored next to the profile. Takes about 1 s.
- Only **active** profiles take part in matching.

### Step 1 — Add a lead

- The job post text is stored in `leads` with a `content_hash` (SHA-256 of the normalised text). The same post pasted twice is detected as a duplicate and not added again.
- Status becomes `new` and a `parse` job is queued.

### Step 2 — Parse (qwen3, ~60–90 s)

- Prompt: `app/llm/prompts/parse.txt`. The model must extract only what the post says and return JSON in a fixed shape (`app/models/lead.py`): title, company, seniority, years required, required skills, nice-to-have skills, tools, domain, location, remote, responsibilities.
- Ollama's structured output forces valid JSON; Pydantic validates it. If the JSON is invalid the call is retried once with the error shown to the model, then the job fails.
- Skill names are normalised in `app/pipeline/skills.py` (lower-case, trimmed, synonyms: `k8s → kubernetes`, `postgres → postgresql`, `node → node.js`, …). The synonym map is a plain dictionary that is easy to extend.
- The model tends to leave *years required* empty even when the post says "5+ years", so `app/pipeline/parse.py` reads "N years / N+ years / N–M years" from the text when the model returns null.
- Status → `parsed`; a `match` job is queued.

### Step 3 — Match (no chat model, ~1 s)

For every active profile three numbers are computed (`app/pipeline/match.py`):

| Part | Weight | How |
|---|---|---|
| semantic | 0.50 | cosine similarity between the lead vector (title + required skills + tools + responsibilities) and the profile vector |
| skill_overlap | 0.35 | share of the post's required skills the person has (skills list **or** mentioned in their bullets); if no required skills, tools are used; if none, 0.5 |
| exp_fit | 0.15 | 1.0 if no years required or the person meets them, otherwise `1 − (required − have) / required` |

`total = 0.50·semantic + 0.35·skill_overlap + 0.15·exp_fit` (weights and thresholds are in `.env`).

Decision:
- Highest total wins → status `matched`, a `tailor` job is queued.
- If the best total is **below 0.50**, or the top two are **less than 0.05 apart**, status becomes `needs_match_review` and a person picks the profile in the UI ("Assign & tailor"). That choice is logged in the history as a manual decision.
- Every profile's scores plus matched/missing skills are stored in `matches`, so the UI can show *why*.

### Step 4 — Tailor + guardrails + ATS (qwen3, ~3–5 min, double if a retry is needed)

Prompt: `app/llm/prompts/tailor.txt`. The model receives the parsed job and the CV **without name and contact details**, and may only:
- rewrite the **summary** (2–3 sentences, facts from the CV only),
- **reorder** the skills list (same skills, most relevant first),
- **rephrase or reorder** the bullets of each role (same number or fewer), keeping company, role and dates identical.

Education and certifications are never sent to the model; code copies them unchanged.

**Guardrails** (`app/pipeline/guardrails.py`) then check the answer:

1. Company, role, start, end must equal the profile → otherwise overwritten and flagged.
2. Skills list may contain only the profile's skills → extras removed, dropped ones re-added.
3. **Every rewritten bullet must trace back to an original bullet of the same role** (at least 35 % of its content words in common) and must not mention a technology that role's original text never mentioned. This catches the model copying the job's responsibilities into the CV or moving a technology from one employer to another.
4. **Invented claims**: any job keyword that appears in the tailored text but nowhere in the original profile.
5. **Gaps**: job keywords the person genuinely lacks. They are shown to the reviewer and never inserted.

If checks 3 or 4 find a problem, the model gets **one stricter retry** listing exactly what was wrong. Anything still wrong after that is **replaced by the original bullet** and flagged, so invented text never reaches the reviewer.

**ATS score** (`app/pipeline/ats.py`): required skills count 2 points, nice-to-have skills and tools 1 point; a keyword counts if the CV text mentions it (any synonym, whole words). Score = points found / points possible × 100, computed for the original profile (`ats_before`) and the tailored CV (`ats_after`). Because only truthful keywords count, before and after are often equal; the score shows *fit*, the side-by-side view shows the tailoring.

Result stored in `generations`; status → `tailored`; a `cover_letter` job is queued.

### Step 5 — Cover letter (qwen3, ~2.5 min)

Prompt: `app/llm/prompts/cover_letter.txt`. Input: parsed job + tailored CV + the list of gaps ("the candidate does NOT have these — never mention them"). Rules: 3–4 short paragraphs, ≤ 250 words, 2–3 concrete achievements from the CV, no clichés, no placeholders, plain text.

Code checks the letter (`app/pipeline/cover_letter.py`): length, clichés such as "I am writing to…", `[placeholders]`, and invented claims. An invented claim triggers one stricter retry; whatever remains is flagged for the reviewer.

Status → `in_review`.

### Step 6 — Human review (UI, "Leads & results")

| Tab | Shows |
|---|---|
| Match | every profile's scores, matched and missing skills, manual override |
| Tailored CV & ATS | ATS before → after, gaps, guardrail flags, original and tailored CV side by side (job keywords highlighted, ✏️ = rewritten bullet), DRAFT banner |
| Cover letter & review | editable summary and letter, **Approve / Reject / Regenerate** |
| Job post | raw text and the parsed JSON |
| History | every status change with who, when and why |

Approve stores the edited text as `final_cv` / `final_cover_letter`, sets the generation to `approved` with reviewer name and time, and moves the lead to `approved`. If there are unresolved error flags, the reviewer must tick "I have checked and fixed the flagged items" first.

### Step 7 — Export (not built yet)

DOCX/PDF export with a "DRAFT — requires human review" line until approved is planned; today results are reviewed on screen.

## 4. Lead statuses

`new → parsed → matched → tailored → in_review → approved | rejected`

Side states: `needs_match_review` (a person must choose the profile), `failed` (a step failed; error shown on the Jobs page, Retry available).

## 5. Guardrail flags you may see

| Code | Severity | Meaning |
|---|---|---|
| `changed_company` / `changed_role` / `changed_start` / `changed_end` | fixed | model altered a fact; profile value restored |
| `experience_count` | fixed | model dropped a role; restored from the profile |
| `too_many_bullets` | fixed | model added bullets; extras dropped |
| `extra_skill` | fixed | skill not in the profile removed from the skills list |
| `restored_skills` | info | skills the model left out were re-added |
| `bullet_not_from_profile` | error | (first pass only) bullet does not match any original bullet of that role, or names a technology that role never had |
| `bullet_restored` / `bullet_dropped` | fixed | after the retry the bullet was still wrong; original restored (or removed if none was free) |
| `invented_claim` | error | a job keyword appears in the tailored CV but not in the original profile |
| `letter_too_long` / `letter_cliche` / `letter_placeholder` / `letter_invented_claim` | error | cover letter checks |

🛑 error = reviewer must check · 🔧 fixed = code already corrected it · ℹ️ info.

## 6. Timing on a CPU-only laptop (8 cores, qwen3:8b)

| Step | Typical time |
|---|---|
| embed profile | 1 s |
| parse | 60–90 s |
| match | 1 s |
| tailor | 3–4.5 min (+ the same again if the stricter retry runs) |
| cover letter | 2.5 min |
| **whole lead** | **≈ 8 min (≈ 12 with a retry)** |

Leads are processed one after another.

## 7. Fake test data (for reference)

Everything below is invented. Never use a real person's CV or contact details in this tool until GRC has approved it.

### 7.1 Sample profiles already in the repository

`data/samples/profiles/`:
- **Sample Person A** — Senior Backend Engineer, 7 years: Python, Django, FastAPI, PostgreSQL, Redis, AWS, Docker, Kubernetes, Terraform, CI/CD, Celery.
- **Sample Person B** — Frontend Engineer, 4 years: JavaScript, TypeScript, React, Next.js, Redux, Tailwind CSS, Node.js, GraphQL, Jest, Figma.

Load them with the "Load sample profiles" button on the Profiles page.

### 7.2 A third profile to paste (Profiles → "Paste / upload JSON")

```json
{
  "name": "Test Person C",
  "headline": "Data Engineer",
  "total_years_experience": 5,
  "summary": "Data engineer with 5 years of experience building batch and streaming data pipelines. Works mainly with Python, SQL, Apache Airflow and Spark on AWS, with a focus on data quality and cost-efficient warehousing.",
  "skills": ["Python", "SQL", "Apache Airflow", "Apache Spark", "dbt", "Snowflake", "PostgreSQL", "Kafka", "AWS", "Docker", "Git", "Data Modeling"],
  "experience": [
    {
      "company": "Example Retail Analytics",
      "role": "Data Engineer",
      "start": "2022-02",
      "end": "present",
      "bullets": [
        "Built 40+ Apache Airflow DAGs loading sales and inventory data into Snowflake every hour.",
        "Rewrote legacy SQL transformations as dbt models, cutting nightly run time from 3 hours to 45 minutes.",
        "Added automated data quality checks that reduced broken dashboard incidents by 70%.",
        "Streamed click events from Kafka into S3 with Spark Structured Streaming."
      ]
    },
    {
      "company": "Sample Telecom Services",
      "role": "Junior Data Engineer",
      "start": "2020-01",
      "end": "2022-01",
      "bullets": [
        "Wrote Python ETL jobs moving billing data from PostgreSQL to an AWS data lake.",
        "Containerised ETL jobs with Docker and scheduled them on AWS ECS.",
        "Designed a star-schema data model used by the finance reporting team."
      ]
    }
  ],
  "education": [
    {"degree": "BS Information Technology", "institution": "Example State University", "year": "2019"}
  ],
  "certifications": ["SnowPro Core Certification"],
  "contact": {"email": "test.person.c@example.com", "phone": "+00 000 0000003", "location": "Example City"}
}
```

### 7.3 A job post to paste (Add leads → "Paste a job post")

Built so that Person C fits but lacks **Databricks, Scala and Terraform** — the tool must list those as gaps and must not put them into the CV or the letter.

```
Senior Data Engineer — Example Energy Group (Remote)

We are building a modern data platform and need a Senior Data Engineer.

Responsibilities:
- Build and maintain data pipelines with Apache Airflow and Apache Spark
- Model data in our warehouse for analytics teams
- Run Spark workloads on Databricks
- Improve data quality monitoring

Requirements:
- 4+ years of data engineering experience
- Python and SQL
- Apache Airflow, Apache Spark
- AWS
- Databricks
- Scala

Nice to have: dbt, Terraform, Kafka
```

### 7.4 Sample job posts in the repository and the expected outcome

`data/samples/leads/` (select them on the Add leads page):

| File | Expected result with profiles A, B and C loaded |
|---|---|
| `lead_01_python_backend.txt` | Person A, clear win (≈ 0.94 vs 0.47) |
| `lead_02_django_developer.txt` | Person A |
| `lead_03_devops_platform.txt` | Person A (≈ 0.76 vs 0.64); gaps Prometheus, Grafana, Bash |
| `lead_04_data_api.txt` | **needs review**: A and C are close (≈ 0.89 vs 0.85) — choose one in the Match tab |
| `lead_05_react_frontend.txt` | Person B, clear win (≈ 0.91 vs 0.44) |
| `lead_06_nextjs_engineer.txt` | Person B |
| `lead_07_ui_component_library.txt` | Person B |
| `lead_08_javascript_web.txt` | Person B |
| `lead_09_fullstack_mixed.txt` | needs review (mixed Python + React post) |
| `lead_10_java_sap.txt` | **needs review**: nobody fits, best score ≈ 0.44 < 0.50 |
| job post from 7.3 | Person C (≈ 0.82); ATS ≈ 71; gaps `terraform, databricks, scala` |

Scores vary slightly between runs because the model's wording differs; the winner and the review decisions should not.

## 8. Known limitations

- CPU inference is slow (see section 6); a GPU or a smaller model (`LLM_MODEL=qwen3:4b`) is faster.
- When the model misbehaves twice, the safe fallback keeps the original bullets, so a tailored CV can end up close to the original except for the summary and skill order. That is intended: truthful beats tailored.
- `ats_after` rarely exceeds `ats_before` for the same reason: only keywords the person really has are counted.
- Export (DOCX/PDF) and automatic collection of job posts from job sites are not built yet.
