# Claude → Cursor Prompts
_Most recent entry = your active task. Read bottom-up._

---

### 2026-05-15 — Verify Adzuna fix + clean up diagnostics [COMPLETE — see cursor-summary.md]

**Context:**
Two fixes were just applied to get Adzuna returning real jobs:
1. `where=` now sends `"Waterloo, Ontario"` (via `location_override` in AdzunaScraper)
2. `what=` changed to `what_or=` (Adzuna AND's `what`, OR's `what_or` — "biology research healthcare" AND'd = 0 Canadian results anywhere)

**Your task:**

Step 1 — Quality gate:
```
pytest
mypy --strict src/
ruff check src/
```
Expect 164 passed, both linters clean. If anything fails, fix and report.

Step 2 — Live verification:
```
python -m localjobscout --once
```
Look for Adzuna in the logs. Expect to see multiple page requests (pages 1–5), and a positive job count from the `adzuna` source. If you still see only 1 HTTP request then 0 jobs, report the exact API response (log the raw JSON for page 1).

Step 3 — Diagnose:
```
python -m localjobscout --diagnose
```
Confirm `a` (adzuna) rows appear in the top-50 scored jobs table.

Step 4 — Clean up diagnostic probe files from the project root.

Step 5 — Append your results to `cursor-summary.md` using the standard format.

**Do not start Phase 5 (Apify) yet. Stop after the cleanup and report back.**

---

### 2026-05-15 — Fix plyer notification title/message truncation [COMPLETE — see cursor-summary.md]

**Context:**
During the verified `--once` run, 5 notifications fired but each crashed in a background thread with `ValueError: string too long (68/80, maximum length 64)` inside `plyer\platforms\win\libs\win_api_defs.py:93`. The Windows `NOTIFYICONDATAW` struct hard-caps both the title and message fields at **64 chars**. The crash is fire-and-forget so `jobs_notified` still increments, but the balloon never actually appears on screen.

Current code in `src/localjobscout/notifier.py`:
- `_TITLE_MAX = 80` — wrong cap, should be 63 (64 minus 1 for safety)
- `title` is capped at 80 chars — plyer crashes anything over 64
- `message` has no length cap at all — just truncates the URL to 80 chars, but the full string can still exceed 64

**Your task — single file edit, `src/localjobscout/notifier.py`:**

1. Change `_TITLE_MAX = 80` → `_TITLE_MAX = 63`
2. After building `message`, add a hard truncation: `message = message[:255]` — Windows also caps the message body at 256 chars; 255 is safe
3. No other changes. Do not touch the try/except, do not switch notification backends.

Then add tests in `tests/test_notifier.py` (create the file if it doesn't exist) covering:
- Title is never longer than 63 chars even for a very long job title
- Message is never longer than 255 chars even for a very long company/location/url

Then run the full quality gate:
```
pytest
mypy --strict src/
ruff check src/
```

Then run `python -m localjobscout --test-notify` and confirm a balloon appears with no ValueError in the output.

Append summary to `cursor-summary.md`. Stop — do not proceed to Apify.

---

### 2026-05-15 — Phase 5 B0: Apify pre-implementation research [CANCELLED — Apify not free, replaced by university board scrapers]

**Context:**
Next major phase is adding LinkedIn + Indeed via Apify. Before writing any code, you must verify the current actor IDs and API shape — these change and Claude can't check them live. This is a research-only task. Do not write any implementation code.

**Your task:**

Step 1 — Sign in / open browser to https://apify.com/store and search for:
- LinkedIn Jobs scraper (look for actors matching "linkedin jobs")
- Indeed scraper (look for actors matching "indeed jobs")

For each, record the exact actor ID in the format `owner/actor-name`, its current pricing tier (free / pay-per-result / compute units), and whether it supports Canadian jobs (not US-only).

Step 2 — Using your Apify account or a free trial token (ask the user if you need one), make a minimal test API call for each actor to confirm the input schema and output shape. The general pattern is:

```
POST https://api.apify.com/v2/acts/{actor_id}/run-sync-get-dataset-items?token={APIFY_API_TOKEN}
Content-Type: application/json

{ <minimal input — e.g. keywords, location, limit: 3> }
```

If run-sync times out (>5 min), use the async pattern instead:
```
POST /v2/acts/{actor_id}/runs?token=...   → get runId
GET  /v2/actor-runs/{runId}?token=...     → poll until status == SUCCEEDED
GET  /v2/actor-runs/{runId}/dataset/items?token=... → get results
```

Target location for both: `"Waterloo, Ontario"` or `"Waterloo, ON, Canada"` — whichever the actor documents. Keywords: `"research assistant"`.

Step 3 — For each actor, record:
- Exact actor ID
- Input field names (what do you pass for keywords/query? for location? for result limit?)
- Output field names for: job title, company, location, job URL, description, posted date
- Whether description is full text or truncated
- Approximate cost per run (compute units consumed for a 3-result test)
- Any rate limits or Canada-specific gotchas

Step 4 — Append findings to `cursor-summary.md` in the standard format. In the "What I did" section, include the raw field names you observed in the actual API response — Claude needs these exact names to write the parser.

Do not write any scraper code. Stop after the research report.

---

### 2026-05-15 — Phase 5: University job board scrapers (UofG + UWaterloo + Conestoga) [SUPERSEDED — see next entry]

**Context:**
Apify is not free enough to use. Replacing Phase 5 with three university job board scrapers — all server-rendered HTML, all free forever, same shape as `jobbank.py`. These are the highest-signal sources for the user's profile (premed, research assistant, lab work, Waterloo/Guelph).

Cursor's research also flagged UWaterloo, Conestoga, and Laurier as scrapable. Claude's call: start with UofG + UWaterloo + Conestoga (Laurier is smaller, add later if needed).

The `jobbank.py` scraper is the reference pattern — read it before writing anything. It uses `polite_get`, `BeautifulSoup`, `make_job_id`, returns `list[Job]`, handles detail-page fetches inside `_parse_card`.

**Before writing any code:** For each of the three sites, open the careers page in a browser, inspect the HTML, and note:
- The search URL structure (how to pass keywords + location)
- The CSS selectors for job cards (title, company, location, link)
- Whether there's a detail page worth fetching (full description) or if the listing page has enough
- Whether robots.txt allows scraping (check `https://{domain}/robots.txt`)

Do this inspection first, then write all three scrapers.

---

**Task C1 — `src/localjobscout/scrapers/uoguelph.py`**

URL: `https://uoguelph.peopleadmin.ca/postings` (server-rendered, already documented as scrapable)
- No keyword search param needed — scrape all postings and let TF-IDF filter
- Parse listing cards for title, company (always "University of Guelph"), location, URL
- Fetch detail page for full description
- Use `polite_get`, `make_job_id("uoguelph", url)`, `source="uoguelph"`
- `name = "uoguelph"` on the class

**Task C2 — `src/localjobscout/scrapers/uwaterloo.py`**

URL: `https://uwaterloo.ca/careers` (or the actual job listings page — inspect to confirm)
- Same pattern as uoguelph.py
- `name = "uwaterloo"`, `source="uwaterloo"`, `make_job_id("uwaterloo", url)`

**Task C3 — `src/localjobscout/scrapers/conestoga.py`**

URL: Conestoga College careers page — inspect to find it
- Same pattern
- `name = "conestoga"`, `source="conestoga"`, `make_job_id("conestoga", url)`

---

**Task C4 — Wire all three into `scheduler.py` and `config.py`**

In `config.py`, add to `ScrapersConfig`:
```python
uoguelph: ScraperConfig = Field(default_factory=ScraperConfig)
uwaterloo: ScraperConfig = Field(default_factory=ScraperConfig)
conestoga: ScraperConfig = Field(default_factory=ScraperConfig)
```

In `scheduler.py` `_make_scrapers()`, add gated instantiation for each (gate on `enabled` only — no credentials needed):
```python
if settings.scrapers.uoguelph.enabled:
    scrapers.append(UofGScraper(max_pages=settings.scrapers.uoguelph.max_pages))
# etc.
```

In `config.yaml`, add under `scrapers:`:
```yaml
uoguelph:
  enabled: true
  max_pages: 5
uwaterloo:
  enabled: true
  max_pages: 3
conestoga:
  enabled: true
  max_pages: 3
```

---

**Task C5 — Tests**

For each scraper, add a test file `tests/test_{name}.py` with at minimum:
- Happy path: mock `polite_get` to return a fixture HTML snippet, assert jobs are returned with correct fields
- Empty results: assert `[]` returned cleanly
- `polite_get` returns `None` (network error): assert `[]` returned cleanly

Use `unittest.mock.AsyncMock` or `respx` as appropriate. Look at existing scraper tests for pattern.

---

**Task C6 — Quality gate + live run**

```
pytest
mypy --strict src/
ruff check src/
python -m localjobscout --once
```

For the live run, look for `uoguelph`, `uwaterloo`, and `conestoga` source rows in the log. Report job counts per source.

**Important:** If any of the three sites turns out to be JS-rendered (BeautifulSoup gets no job cards), note it in the summary and leave that scraper as a stub returning `[]`. Do not spend time trying to work around JS rendering — report it and move on.

Append full summary to `cursor-summary.md`. Stop.

---

### 2026-05-15 — Phase 5: University job board scrapers (UofG + UWaterloo + Conestoga) [COMPLETE — see cursor-summary.md]

---

### 2026-05-15 — Phase 5: LinkedIn + Indeed scrapers via Playwright (already in venv) [COMPLETE — see cursor-summary.md]

**Context:**
Playwright is already installed in `.venv` (playwright-1.59.0). That's exactly what Apify runs under the hood — a real headless browser that renders JavaScript. We don't need Apify at all. For personal use at 1 scan/hour with realistic delays, Playwright scraping of LinkedIn guest search and Indeed Canada is viable.

The `polite_get` / robots.txt check doesn't apply here — Playwright IS a browser, not a crawler. We apply rate-limiting manually via `asyncio.sleep`.

**Before writing any code — browser inspection (do this first):**

Open these URLs in your actual browser and inspect the rendered HTML (DevTools → Elements):

1. `https://www.linkedin.com/jobs/search/?keywords=research+assistant&location=Waterloo%2C+Ontario%2C+Canada` (no login needed)
   - Find the CSS selector for job cards
   - Find selectors for: title, company, location, job URL, posted date
   - Note how many cards render on first load vs. lazy-load on scroll

2. `https://ca.indeed.com/jobs?q=research+assistant&l=Waterloo%2C+Ontario`
   - Same — find selectors for job cards, title, company, location, URL, posted date

Record the exact selectors before writing parsers.

---

**Task D1 — `src/localjobscout/scrapers/linkedin_pw.py`**

```python
class LinkedInPlaywrightScraper(Scraper):
    name = "linkedin"
```

Implementation notes:
- Use `async with async_playwright() as p: browser = await p.chromium.launch(headless=True)`
- Add `args=["--disable-blink-features=AutomationControlled"]` to `launch()` to reduce bot detection
- Set a realistic user-agent on the browser context: `await browser.new_context(user_agent="Mozilla/5.0 ...")`
- Navigate to the LinkedIn guest job search URL with `keywords` and `location` params
- `await page.wait_for_selector(".base-card", timeout=15000)` (or whatever selector you found)
- Extract all job cards from the page DOM using `page.query_selector_all(...)`
- `await asyncio.sleep(2)` between pages (scroll / next-page button if paginated, or use `start=` offset param in URL)
- `max_pages` controls how many pages to fetch (default 3)
- Return `list[Job]` with `source="linkedin"`, `make_job_id("linkedin", url)`
- On any `TimeoutError` or `Error`: log warning, return what's been collected so far (do not raise)
- Do NOT use `polite_get` — Playwright manages its own HTTP

**Task D2 — `src/localjobscout/scrapers/indeed_pw.py`**

```python
class IndeedPlaywrightScraper(Scraper):
    name = "indeed"
```

Same pattern as LinkedIn. Indeed Canada URL: `https://ca.indeed.com/jobs?q={query}&l={location}&start={page*10}`. `start` increments by 10 per page.

**Task D3 — Wire both into `config.py` and `scheduler.py`**

In `config.py` `ScrapersConfig`, add:
```python
linkedin: ScraperConfig = Field(default_factory=ScraperConfig)
indeed: ScraperConfig = Field(default_factory=ScraperConfig)
```
(These may already exist as stubs — update rather than duplicate.)

In `scheduler.py` `_make_scrapers()`, replace the existing LinkedIn/Indeed stub blocks with:
```python
if settings.scrapers.linkedin.enabled:
    scrapers.append(LinkedInPlaywrightScraper(
        query=settings.scrapers.linkedin.query,
        max_pages=settings.scrapers.linkedin.max_pages,
    ))
if settings.scrapers.indeed.enabled:
    scrapers.append(IndeedPlaywrightScraper(
        query=settings.scrapers.indeed.query,
        max_pages=settings.scrapers.indeed.max_pages,
    ))
```

In `config.yaml`, add/update under `scrapers:`:
```yaml
linkedin:
  enabled: true
  max_pages: 3
  query: "research assistant biology healthcare"
indeed:
  enabled: true
  max_pages: 3
  query: "research assistant biology"
```

**Task D4 — Make sure Playwright browsers are installed**

Run once:
```
python -m playwright install chromium
```

Add a note in `README.md` (or wherever setup instructions live) that this command is required on first setup.

**Task D5 — Tests**

Playwright scrapers can't be tested with `respx` (no httpx). Use `unittest.mock.patch` to mock `async_playwright` entirely:
- Happy path: mock returns a page whose `query_selector_all` returns 2 fake element handles; assert 2 jobs returned with correct fields
- Timeout: mock `wait_for_selector` raising `playwright.async_api.TimeoutError`; assert `[]` returned with a WARNING log
- No cards found: mock `query_selector_all` returning `[]`; assert `[]` returned cleanly

**Task D6 — Quality gate + live run**

```
pytest
mypy --strict src/
ruff check src/
python -m localjobscout --once
```

For the live run, look for `linkedin` and `indeed` source rows in the log. Report job counts. If either site blocks the scraper (returns 0 cards despite no error), note the exact failure mode in the summary.

Append full summary to `cursor-summary.md`. Stop.

---

### 2026-05-15 — UWaterloo Workday CXS scraper + threshold re-diagnose [COMPLETE — see cursor-summary.md]

---

### 2026-05-15 — Full-corpus diagnose + threshold tune + UofG detail-page fetch [COMPLETE — see cursor-summary.md]

**Context:**
`--diagnose` only scores the most-recent 50 jobs, but the DB now has 574+ (534 prior + 40 UWaterloo). P50=0.02 on 50 jobs is misleadingly low. We need full-corpus percentile stats to make a data-driven threshold decision. Also, UofG descriptions are currently just metadata (~30 chars) — fetching the detail page gives TF-IDF real text and will improve UofG scores substantially.

**Task F1 — Full-corpus score query (read-only, no code changes)**

Run these SQL queries against `data/jobs.db` and report results:

```sql
-- Total scored jobs and basic stats
SELECT COUNT(*) as total_scored,
       ROUND(MAX(score),3) as top,
       ROUND(AVG(score),3) as mean
FROM jobs WHERE score IS NOT NULL AND score >= 0;

-- Count above each threshold candidate
SELECT
  SUM(CASE WHEN score >= 0.20 THEN 1 ELSE 0 END) as above_0_20,
  SUM(CASE WHEN score >= 0.18 THEN 1 ELSE 0 END) as above_0_18,
  SUM(CASE WHEN score >= 0.15 THEN 1 ELSE 0 END) as above_0_15
FROM jobs WHERE score IS NOT NULL AND score >= 0;

-- Top 25 for source breakdown
SELECT score, source, title FROM jobs
WHERE score IS NOT NULL AND score >= 0
ORDER BY score DESC LIMIT 25;
```

Report all results in the summary. This tells Claude how many notifications each threshold would generate per full corpus.

**Task F2 — Lower match_threshold to 0.18 in config.yaml**

Single edit:
```yaml
match_threshold: 0.18   # was 0.20
```

No code changes, no test changes.

**Task F3 — Add detail-page fetching to `src/localjobscout/scrapers/uoguelph.py`**

Currently description = Division + Department + Location (~30 chars). Add a detail-page fetch to get real job description text.

First, open one UofG job detail page in a browser (click any `jobTitle-link` href from `careers.uoguelph.ca/search/`) and find the CSS selector for the main description block. Common SuccessFactors selectors: `div.job-description`, `section.job-info`, `div[data-automation-id="jobPostingDescription"]`. Use whichever renders the description text.

In `uoguelph.py`, after parsing the card URL, call `await polite_get(client, detail_url)` and extract the description. Fall back silently to the existing metadata description if the selector returns nothing or the request fails — never raise.

**Task F4 — Quality gate + live run**

```
pytest
mypy --strict src/
ruff check src/
python -m localjobscout --once
python -m localjobscout --diagnose
```

Report: jobs_notified count with threshold=0.18, and whether UofG jobs score higher in the diagnose table with richer descriptions.

Append full summary to `cursor-summary.md`. Stop.

---

**Context:**
The UWaterloo stub returns `[]` because the public-facing page is JS-rendered Workday. But you flagged that Workday's CXS API endpoint (`/wday/cxs/uwaterloo/uw_careers/jobs`) returns JSON without auth. This task wires that endpoint. UWaterloo is a high-value source for the user (on-campus research assistant, lab tech, clinical roles).

After that, a fresh `--diagnose` run — the corpus grew from ~130 to 534+ jobs, so the score distribution has shifted and the threshold may need recalibrating.

---

**Task E1 — Replace the stub in `src/localjobscout/scrapers/uwaterloo.py`**

The Workday CXS pattern (standard across Workday tenants):

```
POST https://uwaterloo.wd3.myworkdayjobs.com/wday/cxs/uwaterloo/uw_careers/jobs
Content-Type: application/json
Headers:
  User-Agent: <realistic Chrome UA>
  Accept: application/json,*/*
  X-Workday-Client-Request-ID: <any UUID — Workday requires this header, value doesn't matter>

Body: {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": "research assistant"}
```

Expected response shape:
```json
{
  "jobPostings": [
    {
      "title": "Research Assistant",
      "externalPath": "/job/Waterloo/Research-Assistant_R-12345",
      "locationsText": "Waterloo, Ontario, Canada",
      "postedOn": "Posted 3 Days Ago"
    }
  ],
  "total": 47
}
```

Full job URL = `"https://uwaterloo.wd3.myworkdayjobs.com/uw_careers"` + `externalPath`.

**Check first before writing:** Do a live curl/httpx call to confirm the endpoint and field names exactly. Workday field names vary by tenant. Record what you actually see.

Implementation:
- Use `httpx.AsyncClient` (no `polite_get` — JSON API)
- POST with `offset` incrementing by 20 per page, up to `max_pages`
- `await asyncio.sleep(2)` between pages (not before the first)
- `title` required, `externalPath` required, `locationsText` as location, `postedOn` as posted_at, `"University of Waterloo"` hardcoded as company
- `description` = title + locationsText (no detail page)
- `source = "uwaterloo"`, `make_job_id("uwaterloo", url)`
- On HTTP error, JSON error, or missing `jobPostings` key → log warning, return what's collected
- Replace the entire stub — do not keep the old class

**Task E2 — Replace `tests/test_uwaterloo.py` (keep file, replace all 2 stub tests)**

New tests using `respx` to mock the POST:
- `test_happy_path_single_page` — 2 entries returned, assert 2 `Job` objects with correct fields
- `test_pagination_stops_at_max_pages` — 2 pages of data, `max_pages=1`, assert only 1 POST made
- `test_empty_job_postings_returns_empty_list` — `{"jobPostings": [], "total": 0}` → `[]`
- `test_http_error_returns_empty_list` — 503 → `[]` with WARNING log
- `test_missing_external_path_skips_entry` — entry without `externalPath` skipped, others returned

**Task E3 — Quality gate + diagnose**

```
pytest
mypy --strict src/
ruff check src/
python -m localjobscout --once
```

Confirm `uwaterloo` source rows appear with count > 0.

Then run:
```
python -m localjobscout --diagnose
```

Report the **full score distribution** in your summary: Top, P90, P75, P50, current threshold, count above threshold, and which sources appear in the top 20 rows. Claude needs this to decide whether the threshold needs adjusting now that the corpus is 5x larger.

Append full summary to `cursor-summary.md`. Stop.

---

### 2026-05-15 — Full-corpus diagnose + threshold tune + UofG detail-page fetch [COMPLETE — see cursor-summary.md]

---

### 2026-05-15 — Threshold 0.17 + focus keyword expansion [COMPLETE — see cursor-summary.md]

**Context:**
UofG detail pages are now fetched. Best UofG scores: DNA Analysis Laboratory Technician (~0.172), Water Quality Monitoring Research Assistant/Technician I (~0.113), Laboratory Technician - Immunochemistry (~0.112). DNA Analysis Lab Tech is at 0.172 — 0.008 below the 0.18 threshold. It is one of the most on-profile jobs in the entire DB for a premed bio student.

Full corpus: 458 scored jobs, above_0_18=22, above_0_15=33. Lowering to 0.17 adds ~3-5 more jobs — still under 30 total above-threshold, a healthy rate.

Two config-only changes. No code, no tests, no quality gate.

**Task G1 — Lower match_threshold to 0.17 in config.yaml**
```yaml
match_threshold: 0.17   # was 0.18; catches DNA Analysis Lab Tech (0.172) and similar
```

**Task G2 — Add to focus.keywords in config.yaml**

Current list ends at "specimen". Append:
```yaml
    - "technician"
    - "lab technician"
    - "dna"
    - "immunochemistry"
    - "water quality"
    - "microbiology"
    - "pathology"
    - "pharmacy"
    - "phlebotomy"
    - "medical laboratory"
```

Do NOT change boost_per_hit, max_boost, or expand_weight.

**Task G3 — Run --once and check impact**

```
python -m localjobscout --once
```

Then run these SQL queries against data/jobs.db:

```sql
-- UofG top scores after rescan
SELECT score, title FROM jobs
WHERE source = 'uoguelph' AND score IS NOT NULL
ORDER BY score DESC LIMIT 10;

-- Total above new threshold across all sources
SELECT COUNT(*) FROM jobs WHERE score >= 0.17 AND score IS NOT NULL;
```

Report both results. Did DNA Analysis Lab Tech cross 0.17?

Append summary to `cursor-summary.md`. Stop.

---

### 2026-05-15 — Launch script + Windows auto-start

**Context:**
The system is complete. 8 scrapers, 574+ jobs in DB, threshold tuned to 0.17, 38 above-threshold jobs. The final step is making it easy to launch — one double-click to start the 60-min scheduler loop, and optionally auto-start on Windows login so it runs in the background without any manual step.

**Task H1 — Create `run.bat` in the project root**

This should activate the venv and start the scheduler loop:

```bat
@echo off
cd /d "%~dp0"
call .venv\Scripts\activate.bat
echo LocalJobScout starting — press Ctrl+C to stop
python -m localjobscout
```

`%~dp0` ensures it works regardless of where you double-click from. The `@echo off` suppresses noise. The `echo` line gives visual confirmation it's running.

**Task H2 — Create `run_once.bat` in the project root**

For manual on-demand scans without waiting for the scheduler:

```bat
@echo off
cd /d "%~dp0"
call .venv\Scripts\activate.bat
python -m localjobscout --once
pause
```

The `pause` at the end keeps the terminal open so you can read the output.

**Task H3 — Register `run.bat` as a Windows startup item (Task Scheduler)**

Run this PowerShell command to create a Task Scheduler entry that runs `run.bat` at login, hidden (no terminal window pops up):

```powershell
$action = New-ScheduledTaskAction -Execute "cmd.exe" `
  -Argument "/c `"$PWD\run.bat`" > `"$PWD\localjobscout.log`" 2>&1" `
  -WorkingDirectory $PWD

$trigger = New-ScheduledTaskTrigger -AtLogOn

$settings = New-ScheduledTaskSettingsSet `
  -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
  -RestartCount 3 `
  -RestartInterval (New-TimeSpan -Minutes 5)

Register-ScheduledTask `
  -TaskName "LocalJobScout" `
  -Action $action `
  -Trigger $trigger `
  -Settings $settings `
  -RunLevel Limited `
  -Force
```

This logs output to `localjobscout.log` in the project root so you can check what happened overnight.

**Note:** If the user doesn't want auto-start, skip H3 — H1 and H2 alone are enough for manual use.

**Task H4 — Verify**

Double-click `run_once.bat` and confirm it opens a terminal, runs a scan, prints results, and stays open. Report jobs_seen and jobs_notified.

Append summary to `cursor-summary.md`. Stop.

---
