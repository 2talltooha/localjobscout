# Session Handoff — 2026-06-02

Resume point after PC restart. Read this first.

---

## How to run the program (the part that kept tripping me up)

1. **Be in the project folder** (new terminals start in `C:\Users\awsom`):
   ```powershell
   cd "C:\Users\awsom\OneDrive\Documents\cooode\claudecode\localjobscout"
   ```
2. **Turn on Claude-subscription mode** (so AI features use my Claude subscription,
   NOT a paid API key):
   ```powershell
   $env:LOCALJOBSCOUT_USE_CLI = "1"
   ```
   This env var only lasts for the current terminal window. To make it permanent,
   add this line to `.env` in the project root:
   ```
   LOCALJOBSCOUT_USE_CLI=1
   ```
3. The package is already installed (`pip install -e .` done). Don't reinstall.

### Daily commands
```powershell
python -m localjobscout --once                  # scan all job boards
python -m localjobscout --manual-queue           # show good jobs, ranked
python -m localjobscout --manual-queue --open 3  # + open top 3 live (auto-skips closed)
python -m localjobscout --cover <8-char-id>      # cover letter -> data/applications/
python -m localjobscout --mark applied <id>      # after applying on the site
```

---

## What we changed this session (all committed)

| Commit    | What                                                                 |
|-----------|----------------------------------------------------------------------|
| `176af37` | **Claude subscription backend** — `LOCALJOBSCOUT_USE_CLI=1` routes AI calls through the `claude` CLI instead of an API key. New `src/localjobscout/llm_backend.py`. Optional `LOCALJOBSCOUT_CLI_MODEL=sonnet/opus/haiku`. |
| `13c230d` | **Bug fix** — `_parse_number` crashed the whole scan on weird salary text (lone comma). Scan saved 0 jobs before; now works. |
| `62c24c6` | **Hide closed jobs (age + deadline)** — queue hides jobs older than `queue_max_age_days` (default 30, set in `config.yaml`) and past-deadline jobs. |
| `9789cd1` | **Detect closed postings** — reads "posting closed / position filled" phrases in description; `--open N` re-fetches each URL live and skips + hides closed ones. |
| `f0cd4ba` | **Block credential-gated jobs** — added MLT/lab-tech diploma, "certification required", CSLMS/CSMLS/MLPAO etc. to the suitability filter (caught a clinical-lab posting that slipped through). |

Test suite: **440 passing**, ruff + mypy clean (mypy noise in `tui.py` is pre-existing, unrelated).

---

## Current state of the job queue

- Queue = **41 real jobs** (112 ApexFocusGroup spam rows blocked, credential/closed/stale jobs filtered).
- Scored 60 jobs with the AI judge (via subscription). It keeps flagging top
  University of Guelph roles as *"full-time conflicts with student status"* —
  honest signal that they're reach/part-time-only fits.
- Best clean fit found: a **health care aide** role scored **0.80** (First Aid/CPR
  + care experience, accepts trainees).

---

## OPEN TODO — offered, not yet done

**Make `--suitability` score only queue-eligible jobs.** Right now it scores every
unscored DB row, including the 112 spam rows that never reach the queue — wasting
subscription calls. Fix: filter through `check_suitability` / prefilter before
scoring. User said nothing yet — ask whether to do this.

### Possible next steps
- Implement the `--suitability` efficiency fix above.
- One-time DB sweep: live-check every queued job and hide the dead ones now
  (instead of only when opening).
- `playwright install chromium` to enable LinkedIn + Indeed scraping (currently
  skipped — browser not installed; other 11 boards work).

---

## Notes
- Branch: `master`. All work committed; nothing staged/dirty of importance.
- Adzuna API keys already in `.env`. Anthropic API key NOT set (using subscription instead).
- Caveman response mode is active in this session (user's hook) — cosmetic only.
