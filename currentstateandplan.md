# LocalJobScout — Handoff Document
_Last updated: 2026-05-15 — switching from Cursor to Claude Code_

---

## What This Project Is

Python background service: scrapes 8 job boards on a 60-min schedule, scores listings against a resume using local TF-IDF NLP (zero API cost), fires Windows desktop notifications when a match clears the score threshold.

**User profile:** First-year Biological Science (Honours), University of Guelph, premed. Target roles in Waterloo/Guelph ON: research assistant, lab tech, healthcare aide, pharmacy, clinical admin.

---

## Project Layout

```
localjobscout/
├── config.yaml                   # All tunable settings (threshold, scrapers, keywords)
├── .env                          # Adzuna API credentials (gitignored)
├── run.bat                       # Double-click to start 60-min scheduler loop
├── run_once.bat                  # Double-click to run one scan and see output
├── data/
│   ├── resume.txt                # ~500-word biology/healthcare resume for TF-IDF
│   └── jobs.db                   # SQLite DB, auto-created
├── src/localjobscout/
│   ├── __main__.py               # CLI: --once, --diagnose, --check, --test-notify
│   ├── config.py                 # Pydantic settings loader
│   ├── db.py                     # SQLite layer
│   ├── matcher.py                # TF-IDF cosine similarity + focus keyword boosting
│   ├── resume.py                 # spaCy preprocessing
│   ├── prefilter.py              # Pre-score exclusion rules
│   ├── scheduler.py              # asyncio scan loop + schedule
│   ├── notifier.py               # Windows desktop notifications (plyer, title≤63, msg≤255)
│   └── scrapers/
│       ├── base.py               # Scraper ABC + polite_get (robots.txt + 2s delay)
│       ├── jobbank.py            # Job Bank GC — HTML/BS4 ✅
│       ├── remoteok.py           # RemoteOK JSON API ✅
│       ├── adzuna.py             # Adzuna REST API (what_or=, location_override) ✅
│       ├── uoguelph.py           # UofG SuccessFactors + detail-page fetch ✅
│       ├── conestoga.py          # Conestoga College HTML ✅
│       ├── uwaterloo.py          # UWaterloo Workday CXS JSON API ✅
│       ├── linkedin_pw.py        # LinkedIn via Playwright (guest endpoint) ✅
│       └── indeed_pw.py          # Indeed via Playwright (page 1 before Cloudflare) ✅
└── tests/                        # 167 tests, all passing
```

---

## One-Time Setup

```bash
python -m playwright install chromium   # ~112 MB, required for LinkedIn + Indeed
```

---

## Current State

**Everything is working.** 8 scrapers live, run as `python -m localjobscout` or double-click `run.bat`.

| Source | Jobs in DB | Notes |
|---|---|---|
| jobbank | ~280 | HTML/BS4, query="health biology research" |
| adzuna | ~150 | REST API, what_or=, location="Waterloo, Ontario" |
| uoguelph | 52 | SuccessFactors + detail pages fetched |
| remoteok | ~35 | JSON API |
| linkedin | ~30 | Playwright, guest endpoint, short query required |
| indeed | ~28 | Playwright, Cloudflare blocks page 2+ |
| uwaterloo | ~40 | Workday CXS POST API |
| conestoga | 6 | Plain HTML tables |
| **Total** | **~620** | |

**Test suite:** 186 tests passing. mypy --strict clean (24 source files). ruff clean.

**Score distribution (full corpus, 458 scored):**
- Top: 0.288, Mean: 0.055
- Above threshold (0.17): 38 jobs
- Notable: DNA Analysis Lab Tech @ UofG (0.172), Diagnostic Medical Sonographer (0.23)

---

## Config (current config.yaml values)

```yaml
location: "waterloo, ON"
match_threshold: 0.17
scan_interval_minutes: 60
```

Focus keywords include: premed, biology, biochemistry, laboratory, research assistant, pcr, cell culture, anatomy, physiology, healthcare, clinical, hospital, specimen, technician, lab technician, dna, immunochemistry, water quality, microbiology, pathology, pharmacy, phlebotomy, medical laboratory

Prefilter excludes: master's/PhD/nurse/licensed requirements, jobs requiring 2+ years experience.

---

## Key Gotchas

- **Adzuna** uses `what_or=` not `what=` (AND kills all results). Location must be `"Waterloo, Ontario"` not `"waterloo, ON"`.
- **LinkedIn** AND's keywords — keep query to 2 words max (`"research assistant"`).
- **UofG** old URL (`uoguelph.peopleadmin.ca`) is dead. Live: `careers.uoguelph.ca`.
- **Playwright** must be installed via `python -m playwright install chromium` on first setup.
- **Notifier** title capped at 63 chars, message at 255 chars (Windows struct limits).

---

## What Is Left To Do

### H3 — Windows auto-start (partially done, needs admin)

Task Scheduler registration failed due to non-admin shell. To register auto-start at login:

1. Open PowerShell **as Administrator**
2. `cd` to the project root
3. Run:

```powershell
$action = New-ScheduledTaskAction -Execute "cmd.exe" `
  -Argument "/c `"$PWD\run.bat`" > `"$PWD\localjobscout.log`" 2>&1" `
  -WorkingDirectory $PWD
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet `
  -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
  -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5)
Register-ScheduledTask -TaskName "LocalJobScout" `
  -Action $action -Trigger $trigger -Settings $settings `
  -RunLevel Limited -Force
```

Output logs to `localjobscout.log` in the project root.

### Optional future improvements

All previously-listed optional improvements are now **complete**:

- ✅ **Indeed page 2+** — `playwright-stealth` 2.0 wired into `indeed_pw.py`. Probabilistic bypass; Cloudflare guard still in place as fallback.
- ✅ **Conestoga detail pages** — `_enrich_description` fetches `ViewCompetition.aspx?id=NN` per job and combines Position Summary + Responsibilities + Qualifications sections into the description used by TF-IDF.
- ✅ **Laurier (WLU) scraper** — `src/localjobscout/scrapers/laurier.py` (~160 lines, 6 tests). Wired into `config.py`, `scheduler.py`, `config.yaml`. Live: 14 jobs returned per scan.
- ✅ **--diagnose --all flag** — re-scores the full corpus, prints percentile breakdown (Top / P90 / P75 / P50) + count above threshold.
- ✅ **conftest.py network leak** — layered block: `socket.socket.connect` + `httpx.{,Async}HTTPTransport.handle_{async_,}request` patches gated by `respx.mocks.HTTPCoreMocker.routers` to pass mocked requests through. New `tests/test_network_guard.py` proves the gate.

### New features

- **HTML job-tracking report** (`python -m localjobscout --report` or `report.bat`) — `src/localjobscout/report.py` renders all scored jobs into `data/jobs.html` sorted by match score. Self-contained page (inline CSS / JS, no server). Client-side filters: full-text search, per-source toggles, live threshold slider, "above threshold only", column re-sort.

---

## Run Commands

```bash
python -m localjobscout                    # scheduler loop (60 min)
python -m localjobscout --once             # single scan
python -m localjobscout --diagnose         # score table, top 50 recent
python -m localjobscout --diagnose --all   # score table over full corpus
python -m localjobscout --report           # write HTML report and open it
python -m localjobscout --report --no-open # write report without opening browser
python -m localjobscout --check            # verify notifications work
python -m localjobscout --test-notify      # fire test notification
```

Or double-click `run.bat` / `run_once.bat` / `report.bat` from Windows Explorer.

---

## Credentials (.env, gitignored)

```
ADZUNA_APP_ID=af839bf6
ADZUNA_APP_KEY=a383a24e765aeb04ac080838d2c814ad
```

---

## Workflow (Claude Code edition)

Claude Code reads this file + `claude-prompts.md` for context.
After each task, append to `cursor-summary.md` using the standard format.
Claude (web) writes tasks to `claude-prompts.md`. Claude Code executes them.
Quality gate before marking Complete: `pytest` + `mypy --strict src/` + `ruff check src/`
