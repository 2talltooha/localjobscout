# LocalJobScout — Current State (2026-06-05)

## This Session — Resume Swap + Tailoring Pipeline (4 phases)

Replaced resume with new `Taha_El_Ghadi_Resume.docx` (extracted → `data/resume.txt`,
old backed up to `data/resume.txt.bak`), then built a structured-master → gap-analysis
→ per-job tailored-resume pipeline. Rescored the DB against the new resume.

**Status:** 480 tests pass (was 452), ruff clean, mypy strict clean (44 files).
5 commits on `master`, one per phase + email-attach.

> Note: `data/` is gitignored — `data/resume/master.yaml` and `data/resume.txt`
> live locally only, same as the resume always has.

---

## New Modules / Features

### Phase 1 — `master_resume.py` + `data/resume/master.yaml`
Structured master resume = single source of truth for tailoring. Pydantic-validated
loader, fails loud on malformed input.
- `contact` (core identity), `summaries` (per-profile variants), `items` (tagged).
- Each item: `id`, `section` (experience/projects/education/skills/activities/certs),
  `content` (title/sub/bullets), `tags` (≥1 enforced), `core` (always-include).
- `MasterResume.master_hash()` — stable content hash (gap-cache key).
- `MasterResume.all_text()` — flat corpus for claim validation.
- Seeded from real resume. 12 tests.

### Phase 2 — `gap.py` + `gap_cache` DB table
Fit & gap analysis: each JD requirement → covered/partial/missing + the exact master
item id that covers it; plus ATS keyword coverage.
- LLM claude-haiku, honest-by-construction (unsupported → missing, never invented).
- Cached on `(job_id, master_hash)`: **0 network calls on hit**; changed master
  auto-invalidates (new hash misses).
- `--manual-queue` shows compact cached gap summary per job (read-only, no cost).
- 8 tests.

### Phase 3 — `tailor_resume.py`
`--tailor <job_id>` builds a job-specific resume:
1. classify profile (research / technical / customer-service / general) from JD text
   (word-boundary keyword match),
2. select + order master items by per-profile tag weights + gap-confirmed strengths,
3. hard-drop irrelevant sections (customer-service → no coding/lab items),
4. pick matching summary variant, cap bullets,
5. render guaranteed **one-page PDF** (fpdf2) + Markdown to
   `data/applications/<source>-<id8>/`.
- **Validated vs master before any write**: every line must trace verbatim to a
  master item; summary must be a master variant; off-master = rejected, not saved.
  Reuses cover-letter validator + `forbidden_claims` against master text.
- `--tailor <id> --preview` prints selection, writes nothing.
- Old LLM suggestion report moved to `--tailor-tips <id>` (kept).
- Refactored `cover_letter.validate()` to explicit skip-keywords so master-backed
  facts (lab, customer-service) pass while specific-years claims always flag — all
  old validator tests stay green. Added `fpdf2` to the `pdf` extra.
- 18 + 3 tests.

### Phase 4 — daily-flow wiring (`scheduler.py`)
- End of each scan: auto-tailor top_n queue matches (gap analysis DB-cached, so an
  API call fires only for uncached jobs with a key; tailoring is deterministic).
  Resumes failing master validation are skipped, not written.
- `ScanResult.resumes_tailored` shown in scan panel.
- `--manual-queue` surfaces tailored-resume PDF path per job.
- Email auto-apply attaches the tailored PDF when present (else master resume).
- New config (documented in `config.yaml`): `resume.master_path`, `resume.profiles`
  (per-profile tag-weight overrides), `tailor.auto`, `tailor.top_n`,
  `tailor.max_bullets_per_section`.
- 2 tests; scan-mechanics tests pin `tailor.auto=False` for deterministic counts.

---

## Rescore Results (new resume)

- Rescored 762 jobs · 198 re-excluded · 204 above threshold (0.14).
- Top of list now lab/research/technical-led (Lab Coordinator, Lab Technician –
  Virology, Research Technician) — matches the improved resume's emphasis.
- Practical `--manual-queue` = **41 suitable jobs** (suitability-filtered).
- Absolute TF-IDF magnitudes unchanged (max ~0.296, avg 0.084) — expected; TF-IDF
  measures term overlap, not resume quality. The win is topical ranking + the
  tailoring pipeline, not higher raw scores.

---

## New CLI

```bash
python -m localjobscout --tailor <job_id>            # build tailored resume (PDF+MD)
python -m localjobscout --tailor <job_id> --preview  # print selection, write nothing
python -m localjobscout --tailor-tips <job_id>       # old LLM suggestion report
```
Auto-tailoring also runs during normal scans for the top_n queue matches.

---

## Known Limitations / Follow-ups

1. **Profile classification is heuristic** (`_PROFILE_KEYWORDS` in `tailor_resume.py`).
   Tune keyword sets if a role mis-routes.
2. **Education is core** → a customer-service resume still carries the coursework+lab
   bullet (honest education detail, not a fabricated lab claim). Bullets aren't
   tagged, so no per-bullet relevance filtering. Add tagged bullets if desired.
3. **Senior-role noise** (Chair, Professor, Sergeant) still ranks high in raw TF-IDF
   score; filtered out downstream by suitability/prefilter at queue time.
4. **AI features need a key** — `ANTHROPIC_API_KEY` for gap analysis / suitability /
   cover letters / tips. Tailoring (selection + PDF) is deterministic, no key needed.
5. **Email auto-apply hit rate still 0** — no contact emails in current ATS jobs;
   path (now with tailored-PDF attach) is built and correct.

---

## File Map (new this session)

```
data/resume/master.yaml                   structured master (gitignored)
src/localjobscout/master_resume.py        loader + schema (Phase 1)
src/localjobscout/gap.py                   fit & gap analysis (Phase 2)
src/localjobscout/tailor_resume.py         per-job resume builder (Phase 3)
tests/test_master_resume.py
tests/test_gap.py
tests/test_tailor_resume.py
data/applications/<source>-<id8>/resume.pdf|.md   tailored output
```
