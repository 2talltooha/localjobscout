# LocalJobScout — Project Context for Claude

## What this project is

CLI job-matching tool for **Taha El Ghadi** — first-year Biological Science
(Honours) student at University of Guelph, pre-medicine pathway, Waterloo ON.
Scrapes 13 job boards, scores against resume, sends OS notifications, generates
tailored cover letters, validates letters for fabricated claims, and runs an
interactive auto-apply pipeline.

User: awsomesaws2@gmail.com  
Resume: `data/resume.txt` — no lab experience, CPR/First Aid certified,
trilingual (English, Arabic, French), peer tutor, camp counselor.

Python 3.11+, `src/` layout, hatch build. 392 tests, ruff + mypy clean.

---

## Architecture

```
src/localjobscout/
├── __main__.py       CLI entry point — all argparse commands
├── config.py         Pydantic settings (config.yaml + .env overlay)
├── db.py             SQLite wrapper — Job dataclass + all DB functions
├── scheduler.py      run_scan() async + run_forever() scheduler loop
├── matcher.py        TfidfMatcher + SemanticMatcher + _apply_focus_boost()
├── matching.py       JobFilter, extract_skills, parse_salary
├── prefilter.py      should_exclude() — phrase/years/province/company/regex
├── dedup.py          compute_job_hash(), deduplicate()
├── url_utils.py      normalise_jobbank_url(), normalise_adzuna_url()
├── resume.py         load_resume() — .txt + .pdf (pypdf), get_nlp()
├── suitability.py    LLM suitability scoring via Anthropic API, DB-cached
│                     (prompt caching when a ResumeProfile is supplied)
├── profile.py        AI resume parser → ResumeProfile, file-cached
├── tailor.py         Resume tailoring suggestions (Anthropic)
├── digest.py         Weekly top-N digest email (select + build + send)
├── resume_ab.py      Resume A/B comparison (matcher-agnostic)
├── notifier.py       OS notifications via plyer
├── alerts.py         Email alert sender (AlertConfig)
├── tracker.py        mark_status(), generate_cover_letter(), list_applications()
├── cover_letter.py   generate() + validate() — Anthropic or template backend
├── auto_apply.py     check_suitability(), detect_method(), batch runner,
│                     interactive review, audit log, email sender
├── export.py         write_matches_md() → data/matches.md
├── report.py         write_report() → data/jobs.html
├── prep.py           Interview prep Q&A via Anthropic
├── tui.py            Textual TUI dashboard
└── scrapers/
    ├── base.py           Scraper ABC + polite_get() + robots.txt cache
    ├── jobbank.py        JobBank.gc.ca (httpx + BS4, jsessionid stripped)
    ├── adzuna.py         Adzuna API (tracking params stripped, requires keys)
    ├── linkedin_pw.py    Guest API cards + public page description fetch
    ├── indeed_pw.py      Indeed via Playwright (stealth)
    ├── remoteok.py       RemoteOK JSON API
    ├── uoguelph.py       careers.uoguelph.ca
    ├── uwaterloo.py      uwaterloo.ca/careers
    ├── laurier.py        careers.wlu.ca
    ├── conestoga.py      employment.conestogac.on.ca
    ├── hamiltonhealth.py Hamilton Health Sciences (disabled by default)
    ├── grandriver.py     Grand River Hospital (disabled by default)
    ├── stmarys.py        St. Mary's General (disabled by default)
    └── cambridge.py      Cambridge Memorial (disabled by default)
```

### Data flow
```
Scrapers (async parallel)
  → url_utils normalise URLs
  → enrich (skills, dedup hash)
  → deduplicate (title+company+location hash)
  → upsert DB (INSERT OR IGNORE on id)
  → cross-source dedup (hash_exists_in_db)
  → prefilter (phrases, years-exp, province, company blocklist, title regex)
  → score (TF-IDF + focus boost, or sentence-transformers)
  → update_score DB
  → notify above threshold (OS notification)
  → alert sender (email, optional)
  → auto_export matches.md
```

---

## Database

SQLite at `data/jobs.db`. All columns on `jobs` table:

| column | type | notes |
|--------|------|-------|
| id | TEXT PK | sha256(source:url) — stable after URL normalisation |
| source | TEXT | jobbank/adzuna/linkedin/indeed/uoguelph/etc |
| title, company, location | TEXT | |
| url | TEXT | normalised (no jsessionid/tracking params) |
| description | TEXT | full text; LinkedIn now populated via public page |
| posted_at | TEXT | ISO date if available |
| first_seen | TEXT | ISO timestamp |
| score | REAL | -1.0=excluded, NULL=unscored, 0.0–1.0=TF-IDF score |
| notified | INTEGER | 0/1 |
| salary_min, salary_max | INTEGER | parsed if present |
| job_type | TEXT | |
| skills | TEXT | JSON array |
| job_hash | TEXT | title+company+location hash for cross-source dedup |
| application_status | TEXT | seen/interested/applied/interviewed/offered/rejected/hidden |
| applied_at | TEXT | ISO timestamp, set when status→'applied' |
| cover_letter_path | TEXT | path to generated .md |
| application_notes | TEXT | free text |
| suitability_score | REAL | LLM score 0–1 (NULL until --suitability run) |
| suitability_reason | TEXT | one-sentence LLM explanation |
| qualification_verdict | TEXT | LLM hard-eligibility gate: yes/borderline/no (NULL = unchecked) |
| unmet_requirements | TEXT | JSON array of hard requirements applicant lacks |

---

## Config (`config.yaml`)

Full reference with current defaults:

```yaml
location: "waterloo, ON"
match_threshold: 0.22          # minimum score for notifications + queue
scan_interval_minutes: 60
use_semantic_matcher: false    # true requires pip install localjobscout[semantic]
resume_path: data/resume.txt   # accepts .txt or .pdf
db_path: data/jobs.db

scrapers:
  jobbank:   { enabled: true,  max_pages: 3, query: "" }
  remoteok:  { enabled: true }
  adzuna:    { enabled: true,  max_pages: 5, query: "" }
  linkedin:  { enabled: true,  max_pages: 3, query: "" }
  indeed:    { enabled: true,  max_pages: 3, query: "" }
  uoguelph:  { enabled: true,  max_pages: 3 }
  uwaterloo: { enabled: true,  max_pages: 3, query: "research assistant" }
  conestoga: { enabled: true,  max_pages: 3 }
  laurier:   { enabled: true,  max_pages: 3 }
  hamiltonhealth: { enabled: false }
  grandriver:     { enabled: false }
  stmarys:        { enabled: false }
  cambridge:      { enabled: false }

prefilter:
  exclude_phrases: []                 # case-insensitive substrings to block
  exclude_min_years_experience: 2     # exclude if job requires >2 years exp
  allowed_provinces: ["ON"]           # empty list = allow all provinces
  exclude_companies: []               # company name substrings to block
  exclude_title_regex: "^[A-Z]{2,4}\\d{3}"  # blocks Laurier CTF course postings

focus:
  keywords:
    - research
    - lab
    - laboratory
    - pharmacy
    - clinical
    - biology
    - health
    - patient
    - tutor
    - premed
    - science
    - assistant
    - entry level
  boost_per_hit: 0.05
  max_boost: 0.25
  title_boost_multiplier: 2.0

cover_letter:
  # Phrases always flagged by validate(), regardless of resume content.
  # Add strings here to block claims without touching code.
  forbidden_claims: []

auto_apply:
  enabled: false
  from_email: ""
  smtp_host: "smtp.gmail.com"
  smtp_port: 587
  smtp_password: ""        # use Gmail App Password, keep in .env not here
  min_score: 0.22
  daily_limit: 10
  unattended: false        # if true + --auto-apply-send, skips per-job review

alerts:
  enabled: false
  method: email
  email_smtp_host: "smtp.gmail.com"
  email_smtp_port: 587
  email_from: ""
  email_to: []
  email_password: ""
  min_matches_to_alert: 1
```

---

## Environment Variables

```
ANTHROPIC_API_KEY     cover letters (Anthropic backend), suitability scoring, interview prep
ADZUNA_APP_ID         required for Adzuna scraper
ADZUNA_APP_KEY        required for Adzuna scraper
```

Put these in `.env` at project root. Never commit `.env`.

---

## Full CLI Reference

```bash
# ── Scanning ──────────────────────────────────────────────────────────────
python -m localjobscout --once                   # single scan + exit
python -m localjobscout                          # run forever on schedule
python -m localjobscout --config path/cfg.yaml   # use alternate config

# ── Viewing results ───────────────────────────────────────────────────────
python -m localjobscout --export                 # write data/matches.md
python -m localjobscout --report [--no-open]     # HTML report, auto-opens browser
python -m localjobscout --tui                    # interactive Textual TUI
python -m localjobscout --diagnose [--all]       # score table from DB

# ── Manual submit queue (the main daily driver) ───────────────────────────
python -m localjobscout --manual-queue           # 58 suitable jobs sorted by score
python -m localjobscout --manual-queue --open 3  # + open top 3 URLs in browser
python -m localjobscout --manual-queue --status interested  # only queued jobs

# ── Tracking applications ─────────────────────────────────────────────────
python -m localjobscout --mark applied 9df273eb  # mark job by 8-char ID prefix
python -m localjobscout --mark interested abc123  # statuses: seen interested applied
python -m localjobscout --mark clear abc123       #   interviewed offered rejected hidden
python -m localjobscout --applications            # list all tracked
python -m localjobscout --applications applied    # filter by status
python -m localjobscout --follow-up               # show applied > 7 days ago

# ── Cover letters + prep ──────────────────────────────────────────────────
python -m localjobscout --cover 9df273eb         # generate cover letter
python -m localjobscout --prep 9df273eb          # generate interview Q&A
#   saved to data/applications/<source>-<id>.md

# ── Auto-apply pipeline ───────────────────────────────────────────────────
python -m localjobscout --auto-apply                         # dry run (safe)
python -m localjobscout --auto-apply --auto-apply-send       # live: interactive review
python -m localjobscout --auto-apply --auto-apply-send \
    --yes-send-without-review                                # live: no review prompt
python -m localjobscout --auto-apply --auto-apply-limit 20   # cap jobs processed
python -m localjobscout --auto-apply --auto-apply-score 0.20 # override threshold

# ── Scoring + suitability ─────────────────────────────────────────────────
python -m localjobscout --rescore                # rescore all DB jobs vs current resume
python -m localjobscout --suitability            # LLM-score unscored jobs (needs API key)
python -m localjobscout --suitability-limit 20   # cap API calls

# ── Notifications ─────────────────────────────────────────────────────────
python -m localjobscout --check                  # test notification backend
python -m localjobscout --test-notify            # fire test notification
```

---

## Key Modules — What Each Does

### `prefilter.py`
`should_exclude(job, rules) → (bool, reason)` — applied to every new job during scan.

Active defaults: province=ON only, years-exp cap=2, Laurier CTF regex, no company blocklist.
Province extraction handles all formats: `LocationFlin Flon (MB)`, `Waterloo, ON`, `Ontario`.

### `auto_apply.py — check_suitability(job)`
Second-pass filter running at display/apply time (not during scan) to catch pre-existing
DB rows that predate the prefilter improvements. Blocks: spam companies, CTF postings,
senior title words, credential-required phrases, out-of-province, >2 yrs experience.

Used by both `--auto-apply` and `--manual-queue`.

### `cover_letter.py — validate(letter, resume_text, extra_forbidden=None)`
Returns list of warning strings. Hardcoded patterns catch:
- WHMIS certification claim
- retail / customer service claim
- N years of lab/clinical/professional experience
- hands-on lab/bench experience
- bare "laboratory background/experience", "lab experience", "bench experience"

`extra_forbidden` wires in `cover_letter.forbidden_claims` from config.yaml.
Letters that fail validation are shown with ⚠ warnings in interactive review;
skipped entirely in unattended mode.

### `suitability.py — score_and_cache(job, resume_text)`
Calls claude-haiku-4-5, returns (0–1 float, one-sentence reason). Cached per job_id
in DB — never calls API twice for the same job. Skips gracefully when API key absent
(or uses the `claude` CLI subscription backend when `LOCALJOBSCOUT_USE_CLI=1`).

Same call also produces the **qualification gate**: `qualification_verdict`
(yes/borderline/no) + `unmet_requirements` (JSON list). "no" = posting explicitly
requires a credential/registration/program-enrolment/experience the applicant
lacks → hard-hidden from `--manual-queue`. "borderline" rows show
`⚠ check reqs: <unmet list>` in the queue. Rows cached before the gate existed
(verdict NULL) are re-scored once to backfill on the next suitability pass.

### `linkedin_pw.py — extract_description_from_html(html)`
Pure BeautifulSoup function (no Playwright). After card collection, `fetch()` navigates
to each public job URL (`page.content()` → parse), cap 20 descriptions per run,
1.5s delay between requests. No login, no cookies, no Easy Apply.

### `url_utils.py`
`normalise_jobbank_url()` strips `;jsessionid=...` path params.
`normalise_adzuna_url()` strips `se=`, `v=`, `utm_*` query params.
Applied before `make_job_id()` so the same job gets a stable id across scrape runs.

---

## Current State — Fully Working

- [x] 13 scrapers, all wired, province-filtered, jsessionid/tracking deduped
- [x] LinkedIn descriptions fetched from public pages (BeautifulSoup, no login)
- [x] TF-IDF matching with focus keyword boost
- [x] LLM suitability scoring (Anthropic, DB-cached)
- [x] OS notifications
- [x] Cover letter generation (Anthropic or template), WHMIS/retail/lab-claim validated
- [x] `forbidden_claims` config for user-defined blocked phrases
- [x] `--manual-queue` sorted by combined score, suitability-filtered, `--open N`, `--status`
- [x] `--auto-apply`: interactive review, audit log, email send, portal browser-open
- [x] `--follow-up`: stale application reminders (>7 days)
- [x] `--suitability`: batch LLM scoring with DB caching
- [x] `--rescore`: rescore all DB jobs against updated resume
- [x] PDF resume support (pypdf)
- [x] Application tracking, cover letters, interview prep
- [x] AI resume profile parser (profile.py, cached to data/resume_profile.json)
- [x] Resume tailoring suggestions (`--tailor`, tailor.py)
- [x] Inline suitability scoring during scan + prompt caching
- [x] Salary-aware ranking (config-gated, parsed from description)
- [x] Application deadline tracking (`--deadlines`, parsed from description)
- [x] Weekly digest email (`--digest`, digest.py)
- [x] Resume A/B comparison (`--compare-resumes`, resume_ab.py)
- [x] Indeed full-description fetch (parity with LinkedIn)
- [x] TUI, HTML report, markdown export
- [x] 392 tests, ruff clean, mypy strict clean

---

## Known Remaining Issues

1. **Indeed description quality** — RESOLVED. `indeed_pw.py` now fetches full
   descriptions from each `viewjob` page (BeautifulSoup, capped at 20/run), same
   pattern as LinkedIn.

2. **Email apply hit rate = 0** — no email addresses in current ATS job descriptions.
   Email send path is built and correct; just no matching jobs in current DB.
   Will fire automatically when/if small employers post with contact emails.

3. **LinkedIn/Indeed Easy Apply not automated** — intentional. ToS + ban risk.
   These stay manual-submit; the tool opens the URL in browser.

4. **Keyword injection in cover letters reads oddly** — RESOLVED. `_relevant_keywords()`
   now maps focus keywords to natural noun phrases (`_KEYWORD_PHRASES`) and skips
   keywords that don't read as nouns.

5. **Pre-existing DB rows from before prefilter improvements** — ~69 unsuitable rows
   (BC/MB jobs, professor roles) still in DB at score 0.14–0.29. They're invisible to
   `--manual-queue` (check_suitability filters them) but show up in `--export` and TUI.
   Resolve by deleting `data/jobs.db` and re-scanning, or marking them 'hidden'.

6. **Optional deps for AI/semantic** — install `localjobscout[ai]` for Anthropic
   features and `localjobscout[semantic]` for the sentence-transformers matcher.
   Both degrade gracefully when absent.

---

## Chat Display Conventions (Taha's preferences)

After any scan or when asked for results, show a **top-5 markdown chart** in
chat with columns: `# | Score | Job | Employer | Notes | Apply` — the Apply
column is a markdown link to the posting URL. Mention jobs hidden by the
qualification gate below the chart.

When Taha says **"5 more"**, show the next 5 ranked jobs from the DB
(same filters: check_suitability + qualification_verdict != 'no', combined
score order, skip already-shown ranks). Dig below match_threshold if the
queue is exhausted, flagging that those are lower-confidence.

---

## Scheduled Automation (Windows Task Scheduler)

Task **"LocalJobScout Scan"** runs hourly while Taha is logged in:
`wscript.exe scripts/run_scan_hidden.vbs` → `scripts/scheduled_scan.bat` →
venv python `-m localjobscout --once` with `LOCALJOBSCOUT_USE_CLI=1`
(LLM suitability+qualification via Claude CLI subscription, no API key).
Output appended to `data/scan_task.log`. OS notifications fire on new matches.

Manage: `schtasks /query /tn "LocalJobScout Scan"` · delete with
`schtasks /delete /tn "LocalJobScout Scan" /f`.

Email alerts (`alerts:` in config.yaml) remain off until a Gmail App Password
is generated (https://myaccount.google.com/apppasswords) and put in `.env` as
`AUTO_APPLY_SMTP_PASSWORD=...`.

---

## File Locations

```
data/
├── resume.txt              applicant resume (.txt or .pdf)
├── jobs.db                 SQLite database
├── matches.md              auto-exported ranked job list
├── jobs.html               HTML report
├── scan_task.log           scheduled-task scan output (append-only)
├── auto_apply_log.jsonl    audit log — one JSON line per processed job
└── applications/           cover letters + interview prep (.md files)

config.yaml                 main config (edit before first run)
.env                        ANTHROPIC_API_KEY, ADZUNA_APP_ID, ADZUNA_APP_KEY
```

---

## Test Suite

```bash
pytest                                  # 265 tests
pytest tests/test_url_utils.py          # jsessionid + Adzuna normalisation
pytest tests/test_prefilter.py          # province, spam, CTF filter
pytest tests/test_cover_letter_validation.py  # fabricated-claim detection
pytest tests/test_linkedin_pw.py        # LinkedIn scraper + description extraction
```

Key regression tests:
- `test_jobbank_different_jsessionids_produce_same_url` — dedup fix
- `test_province_filter_blocks_bc_job` — Ontario-only filter
- `test_ctf_title_regex_blocks_laurier_course_code` — CTF filter
- `test_bare_laboratory_background_flagged` — fabrication guard
- `test_description_populated_when_content_returns_html` — LinkedIn description
