"""BD Lead-to-CV Tool — Streamlit UI.

Run from the project root (the worker must be running too):
    streamlit run ui/streamlit_app.py
"""

from __future__ import annotations

import html
import json
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import services  # noqa: E402
from app.db import repo  # noqa: E402
from app.pipeline.skills import find_mentions  # noqa: E402

st.set_page_config(page_title="BD Lead-to-CV", page_icon="📄", layout="wide")

STEP_OF_STATUS = {"new": 0, "parsed": 1, "matched": 2, "needs_match_review": 2,
                  "tailored": 3, "in_review": 4, "approved": 4, "rejected": 4}
STEPS = ["Parse job post", "Match profile", "Tailor CV", "Cover letter"]
ALL_STATUSES = ["new", "parsed", "matched", "needs_match_review", "tailored",
                "in_review", "approved", "rejected", "failed"]


def reviewer() -> str:
    return st.session_state.get("reviewer") or "reviewer"


def watch_until_changed(fingerprint) -> None:
    """While work is running, check every 4 s and refresh the page when something changes."""
    start = fingerprint()

    @st.fragment(run_every=4)
    def _watch() -> None:
        if fingerprint() != start:
            st.rerun()

    _watch()


def highlight(text: str, keywords: list[str]) -> str:
    """HTML-escape text and wrap every mention of a job keyword in <mark>."""
    spans = sorted(span for kw in keywords for span in find_mentions(text, kw))
    out, pos = [], 0
    for start, end in spans:
        if start < pos:
            continue
        out.append(html.escape(text[pos:start]))
        out.append(f"<mark>{html.escape(text[start:end])}</mark>")
        pos = end
    out.append(html.escape(text[pos:]))
    return "".join(out)


def show_html(content: str) -> None:
    st.markdown(content, unsafe_allow_html=True)


def local_time(ts, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """Database times are UTC; show them in this machine's local time zone."""
    return ts.astimezone().strftime(fmt) if ts else ""


def dev_data_warning() -> None:
    st.warning("**Development mode — use fake data only.** Do not enter a real person's CV, contact "
               "details or other personal data until GRC has approved it (Aqua AI Acceptable Use Policy).",
               icon="⚠️")


def sidebar() -> None:
    st.sidebar.text_input("Your name (for the review log)", key="reviewer", placeholder="reviewer")
    try:
        q = repo.queue_summary()
    except Exception as exc:
        st.sidebar.error(f"Database not reachable: {exc}")
        st.stop()
    st.sidebar.markdown("**Job queue**")
    c1, c2, c3 = st.sidebar.columns(3)
    c1.metric("Queued", q.get("queued", 0))
    c2.metric("Running", q.get("running", 0))
    c3.metric("Failed", q.get("failed", 0))
    if q.get("queued", 0) and not q.get("running", 0) and q["oldest_queued_seconds"] > 15:
        st.sidebar.error("Jobs are waiting but nothing is running. Is the worker started?\n\n"
                         "`python -m app.worker.worker`")
    if q.get("queued", 0) or q.get("running", 0):
        st.sidebar.info("⏳ Processing… this can take a few minutes on CPU.")


def page_profiles() -> None:
    st.header("1 · Profiles")
    dev_data_warning()

    profiles = repo.list_profiles()
    if profiles:
        st.dataframe(pd.DataFrame([{
            "id": p["id"], "name": p["display_name"], "headline": p["cv"].get("headline"),
            "years": p["cv"].get("total_years_experience"), "skills": len(p["cv"].get("skills", [])),
            "embedded": "✅" if p["has_embedding"] else "⏳", "active": "✅" if p["is_active"] else "—",
        } for p in profiles]), hide_index=True, width="stretch")
        if any(not p["has_embedding"] for p in profiles):
            watch_until_changed(lambda: [p["has_embedding"] for p in repo.list_profiles()])
    else:
        st.info("No profiles yet. Create one below, or load the two sample profiles.")

    tab_form, tab_json, tab_samples, tab_view = st.tabs(
        ["➕ Create with form", "📋 Paste / upload JSON", "🧪 Load sample profiles", "👁 View / manage"])

    with tab_form:
        profile_form()
    with tab_json:
        profile_json_editor(profiles)
    with tab_samples:
        st.write("Loads the two fake profiles from `data/samples/profiles/` "
                 "(a Python/cloud backend engineer and a React frontend engineer).")
        if st.button("Load sample profiles"):
            ids = services.load_sample_profiles()
            st.success(f"Loaded {len(ids)} sample profiles. They are being embedded in the background.")
            st.rerun()
    with tab_view:
        for p in profiles:
            with st.expander(f"{p['display_name']} — {p['cv'].get('headline', '')}"):
                render_cv(p["cv"])
                label = "Deactivate (exclude from matching)" if p["is_active"] else "Activate"
                if st.button(label, key=f"toggle_{p['id']}"):
                    repo.set_profile_active(p["id"], not p["is_active"])
                    st.rerun()


def profile_form() -> None:
    st.caption("Saving with an existing name replaces that profile.")
    c1, c2 = st.columns(2)
    n_roles = c1.number_input("Number of jobs (experience entries)", 1, 10, 2)
    n_edu = c2.number_input("Number of education entries", 0, 5, 1)

    with st.form("profile_form"):
        c1, c2, c3 = st.columns([2, 2, 1])
        name = c1.text_input("Full name *", placeholder="Test Person C")
        headline = c2.text_input("Headline *", placeholder="Senior Data Engineer")
        years = c3.number_input("Total years *", 0.0, 50.0, 3.0, 0.5)
        c1, c2, c3 = st.columns(3)
        email = c1.text_input("Email", placeholder="test.person@example.com")
        phone = c2.text_input("Phone", placeholder="+00 000 0000000")
        location = c3.text_input("Location", placeholder="Example City")
        summary = st.text_area("Summary *", height=100)
        skills = st.text_area("Skills * (comma-separated)", placeholder="Python, SQL, Airflow, AWS")
        certs = st.text_area("Certifications (one per line)", height=70)

        roles = []
        for i in range(int(n_roles)):
            st.markdown(f"**Job {i + 1}**")
            c1, c2, c3, c4 = st.columns([2, 2, 1, 1])
            roles.append({
                "company": c1.text_input("Company", key=f"co{i}"),
                "role": c2.text_input("Role / title", key=f"ro{i}"),
                "start": c3.text_input("Start (YYYY-MM)", key=f"st{i}"),
                "end": c4.text_input("End (YYYY-MM or present)", key=f"en{i}", value="present" if i == 0 else ""),
                "bullets": st.text_area("Achievements (one per line)", key=f"bu{i}", height=110),
            })
        education = []
        for i in range(int(n_edu)):
            st.markdown(f"**Education {i + 1}**")
            c1, c2, c3 = st.columns([2, 2, 1])
            education.append({"degree": c1.text_input("Degree", key=f"de{i}"),
                              "institution": c2.text_input("Institution", key=f"in{i}"),
                              "year": c3.text_input("Year", key=f"ye{i}")})

        if not st.form_submit_button("Save profile", type="primary"):
            return

    cv = {
        "name": name.strip(), "headline": headline.strip(), "total_years_experience": years,
        "summary": summary.strip(),
        "skills": [s.strip() for s in skills.split(",") if s.strip()],
        "experience": [{**r, "bullets": [b.strip() for b in r["bullets"].splitlines() if b.strip()]}
                       for r in roles if r["company"].strip() and r["role"].strip()],
        "education": [e for e in education if e["degree"].strip()],
        "certifications": [c.strip() for c in certs.splitlines() if c.strip()],
        "contact": {"email": email.strip(), "phone": phone.strip(), "location": location.strip()},
    }
    problems = [label for label, ok in [("name", cv["name"]), ("headline", cv["headline"]),
                                        ("summary", cv["summary"]), ("skills", cv["skills"]),
                                        ("at least one job with company and role", cv["experience"])] if not ok]
    if problems:
        st.error("Please fill in: " + ", ".join(problems))
        return
    try:
        services.save_profile(cv)
    except Exception as exc:
        st.error(f"Could not save profile: {exc}")
        return
    st.success(f"Saved profile '{cv['name']}'. It is being embedded in the background.")


def profile_json_editor(profiles: list[dict]) -> None:
    st.caption("Paste a profile in the JSON format of `data/samples/profiles/*.json`, "
               "or pick an existing profile to edit it.")
    options = {"(new)": None} | {p["display_name"]: p for p in profiles}
    choice = st.selectbox("Start from", list(options))
    uploaded = st.file_uploader("…or upload a .json file", type="json")
    initial = (uploaded.getvalue().decode("utf-8") if uploaded
               else json.dumps(options[choice]["cv"], indent=2) if options[choice]
               else (services.SAMPLES_DIR / "profiles" / "sample_person_a.json").read_text())
    text = st.text_area("Profile JSON", initial, height=400, key=f"json_{choice}_{bool(uploaded)}")
    if st.button("Save JSON profile", type="primary"):
        try:
            services.save_profile(json.loads(text))
            st.success("Saved. It is being embedded in the background.")
        except Exception as exc:
            st.error(f"Could not save: {exc}")


def render_cv(cv: dict, keywords: list[str] | None = None, original: dict | None = None) -> None:
    """Show a CV. Job keywords are highlighted; bullets that differ from `original` are marked ✏️."""
    kws = keywords or []
    show_html(f"<h4>{html.escape(cv.get('name', ''))}</h4><b>{html.escape(cv.get('headline', ''))}</b> · "
              f"{cv.get('total_years_experience', '')} years")
    show_html("<b>Summary</b><br>" + highlight(cv.get("summary", ""), kws))
    show_html("<b>Skills</b><br>" + highlight(", ".join(cv.get("skills", [])), kws))
    st.markdown("**Experience**")
    for i, job in enumerate(cv.get("experience", [])):
        show_html(f"<i>{html.escape(job['role'])} — {html.escape(job['company'])} "
                  f"({html.escape(job['start'])} – {html.escape(job['end'])})</i>")
        orig_bullets = set(original["experience"][i]["bullets"]) if original else set()
        items = "".join(
            f"<li>{'✏️ ' if original and b not in orig_bullets else ''}{highlight(b, kws)}</li>"
            for b in job.get("bullets", []))
        show_html(f"<ul>{items}</ul>")
    if cv.get("education"):
        st.markdown("**Education**")
        for e in cv["education"]:
            st.text(f"{e['degree']} — {e['institution']} ({e['year']})")
    if cv.get("certifications"):
        st.markdown("**Certifications**")
        for c in cv["certifications"]:
            st.text(c)


def page_add_leads() -> None:
    st.header("2 · Add leads")
    if not repo.list_profiles(active_only=True):
        st.warning("There are no active profiles yet. Add one on the **Profiles** page first, "
                   "otherwise matching will fail.")

    st.subheader("Option A — use sample job posts")
    files = services.sample_lead_files()
    names = [f.name for f in files]
    default = [n for n in names if n.startswith(("lead_01", "lead_05", "lead_09"))]
    chosen = st.multiselect("Sample leads", names, default=default)
    with st.expander("Preview selected"):
        for f in files:
            if f.name in chosen:
                st.markdown(f"**{f.name}**")
                st.text(f.read_text(encoding="utf-8"))
    if st.button("Add selected sample leads", type="primary", disabled=not chosen):
        report_added([services.add_lead(f.read_text(encoding="utf-8"), source="sample", changed_by=reviewer())
                      for f in files if f.name in chosen])

    st.divider()
    st.subheader("Option B — paste a job post")
    with st.form("lead_form", clear_on_submit=True):
        raw = st.text_area("Job post text *", height=260)
        c1, c2 = st.columns(2)
        company = c1.text_input("Company (optional)")
        url = c2.text_input("URL (optional)")
        if st.form_submit_button("Add lead", type="primary"):
            try:
                report_added([services.add_lead(raw, company=company, source_url=url, changed_by=reviewer())])
            except ValueError as exc:
                st.error(str(exc))


def report_added(results: list[tuple[int, bool]]) -> None:
    new = [i for i, created in results if created]
    dupes = [i for i, created in results if not created]
    if new:
        st.success(f"Added lead(s) {', '.join(map(str, new))}. Processing started automatically — "
                   "open **Leads & results** to follow along.")
    if dupes:
        st.info(f"Already in the system (skipped duplicates): lead(s) {', '.join(map(str, dupes))}.")


def page_leads() -> None:
    st.header("3 · Leads & results")
    status = st.selectbox("Filter by status", ["(all)"] + ALL_STATUSES)
    leads = repo.list_leads(None if status == "(all)" else status)
    if not leads:
        st.info("No leads yet. Add some on the **Add leads** page.")
        return

    st.dataframe(pd.DataFrame([{
        "id": l["id"], "title": l["title"] or "(parsing…)", "company": l["company"], "source": l["source"],
        "status": l["status"], "assigned profile": l["assigned_profile"],
    } for l in leads]), hide_index=True, width="stretch")

    labels = {l["id"]: f"#{l['id']} · {l['title'] or '(parsing…)'} · {l['status']}" for l in leads}
    lead_id = st.selectbox("Open lead", list(labels), format_func=lambda i: labels.get(i, i), key="open_lead")
    st.divider()
    lead_detail(lead_id)


def lead_detail(lead_id: int) -> None:
    lead = repo.get_lead(lead_id)
    pending = repo.pending_jobs_for_lead(lead_id)
    generation = repo.get_latest_generation(lead_id)

    st.subheader(f"#{lead['id']} · {lead['title'] or 'New lead'}"
                 + (f" — {lead['company']}" if lead["company"] else ""))
    pipeline_progress(lead, pending)
    if pending:
        watch_until_changed(lambda: (repo.get_lead(lead_id)["status"], len(repo.pending_jobs_for_lead(lead_id))))

    tabs = st.tabs(["🎯 Match", "📝 Tailored CV & ATS", "✉️ Cover letter & review", "📄 Job post", "🕓 History"])
    with tabs[0]:
        match_tab(lead)
    with tabs[1]:
        tailored_tab(lead, generation)
    with tabs[2]:
        review_tab(lead, generation, busy=bool(pending))
    with tabs[3]:
        c1, c2 = st.columns(2)
        c1.markdown("**Raw post**")
        c1.text(lead["raw_text"])
        c2.markdown("**Parsed by the AI**")
        c2.json(lead["parsed"] or {})
    with tabs[4]:
        st.dataframe(pd.DataFrame([{**h, "changed_at": local_time(h["changed_at"])}
                                   for h in repo.get_status_history(lead_id)]), hide_index=True, width="stretch")


def pipeline_progress(lead: dict, pending: list[dict]) -> None:
    done = STEP_OF_STATUS.get(lead["status"], 0)
    running = {j["job_type"] for j in pending}
    job_of_step = ["parse", "match", "tailor", "cover_letter"]
    cols = st.columns(4)
    for i, (col, label) in enumerate(zip(cols, STEPS)):
        if job_of_step[i] in running:
            col.info(f"⏳ {label}")
        elif i < done:
            col.success(f"✅ {label}")
        else:
            col.container(border=True).write(f"◻️ {label}")

    if lead["status"] == "failed":
        last = repo.get_status_history(lead["id"])[-1]
        st.error(f"**This lead failed.** {last['note']}\n\nFix the cause, then retry the job on the **Jobs** page.")
    elif lead["status"] == "needs_match_review":
        st.warning("**Matching was not confident — please choose a profile** in the Match tab.")
    elif pending:
        st.info("⏳ Processing… this can take a few minutes on CPU. The page refreshes by itself.")


def match_tab(lead: dict) -> None:
    matches = repo.get_matches(lead["id"])
    if not matches:
        st.info("Not matched yet.")
        return
    st.caption("total = 0.50 × semantic similarity + 0.35 × required-skill overlap + 0.15 × experience fit")
    st.dataframe(pd.DataFrame([{
        "chosen": "⭐" if m["is_chosen"] else "", "profile": m["display_name"],
        "total": round(m["total"], 3), "semantic": round(m["semantic"], 3),
        "skill overlap": round(m["skill_overlap"], 3), "experience fit": round(m["exp_fit"], 3),
        "matched skills": ", ".join(m["matched_skills"] or []),
        "missing skills": ", ".join(m["missing_skills"] or []),
    } for m in matches]), hide_index=True, width="stretch")

    profiles = {p["id"]: p["display_name"] for p in repo.list_profiles(active_only=True)}
    current = lead["assigned_profile_id"]
    c1, c2 = st.columns([3, 1])
    pick = c1.selectbox("Use this profile", list(profiles), format_func=lambda i: profiles.get(i, i),
                        index=list(profiles).index(current) if current in profiles else 0,
                        key=f"pick_profile_{lead['id']}")
    if c2.button("Assign & tailor", type="primary" if lead["status"] == "needs_match_review" else "secondary"):
        services.choose_profile(lead["id"], pick, reviewer())
        st.rerun()


def tailored_tab(lead: dict, generation: dict | None) -> None:
    if not generation or not generation["tailored_cv"]:
        st.info("No tailored CV yet.")
        return
    parsed = lead["parsed"] or {}
    keywords = parsed.get("required_skills", []) + parsed.get("nice_to_have_skills", []) + parsed.get("tools", [])
    original = repo.get_profile(generation["profile_id"])["cv"]
    tailored = generation["final_cv"] or generation["tailored_cv"]

    c1, c2, c3 = st.columns(3)
    c1.metric("ATS score before", f"{generation['ats_before']:.0f} / 100")
    c2.metric("ATS score after", f"{generation['ats_after']:.0f} / 100",
              delta=f"{generation['ats_after'] - generation['ats_before']:+.0f}")
    errors = [f for f in generation["guardrail_flags"] or [] if f["severity"] == "error"]
    c3.metric("Guardrail errors", len(errors))
    st.caption("The ATS score only counts job keywords the CV truthfully contains. Keywords the person "
               "doesn't have are listed as gaps below and are never added.")

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Gaps** — job keywords this person does not have")
        st.write(", ".join(generation["gaps"] or []) or "None 🎉")
    with c2:
        st.markdown("**Guardrail flags**")
        flags = generation["guardrail_flags"] or []
        if not flags:
            st.write("None — nothing had to be corrected.")
        for f in flags:
            icon = {"error": "🛑", "fixed": "🔧", "info": "ℹ️"}.get(f["severity"], "•")
            st.write(f"{icon} {f['message']}")

    st.divider()
    st.caption("Highlighted = job keywords.  ✏️ = bullet rewritten by the AI.")
    left, right = st.columns(2)
    with left:
        st.markdown("### Original profile")
        render_cv(original, keywords)
    with right:
        st.markdown("### Tailored CV" + (" (edited)" if generation["final_cv"] else ""))
        if generation["status"] != "approved":
            st.error("DRAFT — requires human review")
        render_cv(tailored, keywords, original=original)


def review_tab(lead: dict, generation: dict | None, busy: bool) -> None:
    if not generation:
        st.info("Nothing to review yet.")
        return
    if not generation["cover_letter"]:
        st.info("The cover letter is still being written…" if busy else "No cover letter yet.")
        return

    status = generation["status"]
    if status == "approved":
        st.success(f"✅ Approved by {generation['reviewer']} on {local_time(generation['approved_at'], '%Y-%m-%d %H:%M')}")
    elif status == "rejected":
        st.warning(f"Rejected by {generation['reviewer']}.")
    else:
        st.error("DRAFT — requires human review")

    tailored = generation["final_cv"] or generation["tailored_cv"]
    letter = generation["final_cover_letter"] or generation["cover_letter"]
    key = f"gen{generation['id']}"
    edited_summary = st.text_area("Tailored CV summary (editable)", tailored["summary"], height=110, key=f"{key}_s")
    edited_letter = st.text_area("Cover letter (editable)", letter, height=380, key=f"{key}_l")
    st.caption(f"{len(edited_letter.split())} words (limit 250) · model {generation['llm_model']} · "
               f"prompts {generation['prompt_version']}")

    errors = [f for f in generation["guardrail_flags"] or [] if f["severity"] == "error"]
    checked = True
    if errors and status == "draft":
        st.warning(f"There are {len(errors)} guardrail error(s) — see the **Tailored CV & ATS** tab.")
        checked = st.checkbox("I have checked and fixed the flagged items", key=f"{key}_chk")

    final_cv = {**tailored, "summary": edited_summary}
    c1, c2, c3 = st.columns(3)
    if c1.button("✅ Approve", type="primary", disabled=busy or not checked or status != "draft", key=f"{key}_a"):
        services.review(lead["id"], generation["id"], True, reviewer(), edited_letter, final_cv)
        st.rerun()
    if c2.button("✖ Reject", disabled=busy or status != "draft", key=f"{key}_r"):
        services.review(lead["id"], generation["id"], False, reviewer(), edited_letter, final_cv)
        st.rerun()
    if c3.button("🔄 Regenerate (new draft)", disabled=busy, key=f"{key}_g"):
        services.regenerate(lead["id"], reviewer())
        st.rerun()


def page_jobs() -> None:
    st.header("4 · Jobs")
    jobs = repo.list_jobs()
    if not jobs:
        st.info("No jobs yet.")
        return
    if any(j["status"] in ("queued", "running") for j in jobs):
        watch_until_changed(lambda: [(j["id"], j["status"]) for j in repo.list_jobs(50)])

    def duration(j: dict) -> str:
        if j["started_at"] and j["finished_at"]:
            return f"{(j['finished_at'] - j['started_at']).total_seconds():.0f}s"
        return "…" if j["status"] == "running" else ""

    st.dataframe(pd.DataFrame([{
        "id": j["id"], "type": j["job_type"], "status": j["status"], "lead": j["payload"].get("lead_id"),
        "profile": j["payload"].get("profile_id"), "attempts": j["attempts"], "duration": duration(j),
        "created": local_time(j["created_at"], "%H:%M:%S"),
    } for j in jobs]), hide_index=True, width="stretch")

    failed = [j for j in jobs if j["status"] == "failed"]
    if failed:
        st.subheader("Failed jobs")
    for j in failed:
        with st.container(border=True):
            st.markdown(f"**Job {j['id']} · {j['job_type']}** (lead {j['payload'].get('lead_id', '—')})")
            st.code(j["error"] or "(no error message)")
            if st.button("Retry", key=f"retry_{j['id']}"):
                repo.requeue_job(j["id"])
                st.rerun()


sidebar()
page = st.navigation([
    st.Page(page_profiles, title="1 · Profiles", icon="👤", url_path="profiles"),
    st.Page(page_add_leads, title="2 · Add leads", icon="➕", url_path="add-leads"),
    st.Page(page_leads, title="3 · Leads & results", icon="📋", url_path="leads"),
    st.Page(page_jobs, title="4 · Jobs", icon="⚙️", url_path="jobs"),
])
page.run()
