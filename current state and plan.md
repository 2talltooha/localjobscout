EXECUTION RULES:
- Follow tasks in order
- Do not skip ahead
- Do not refactor unrelated code
- Stop after each task

Here is the full architectural plan.

---

# LocalJobScout — Matching Improvement Plan

---

## Executive Summary

The system isn't broken — it's applying a technically correct algorithm to a fundamentally mismatched input. The resume describes retail customer service; the target jobs describe biology and clinical work. These two document sets share no vocabulary after stopword removal, so TF-IDF cosine similarity is near zero regardless of threshold. The fix is to give the vectorizer something to work with.

The recommended solution is **resume vocabulary expansion** in Phase 1: at score time (not stored anywhere), append the user's own focus keywords to the resume text before vectorization. This costs zero new dependencies, changes roughly 15 lines of code, and is fully reversible with a single config flag. Combined with a **title-priority boost** (Phase 2, ~8 lines) and a **score diagnostic CLI command** (Phase 3), the system should reliably produce notifications for appropriate premed/biology jobs without false positives. The semantic matcher (`SemanticMatcher`, already architecturally planned in `build_matcher()`'s TODO comment and in `pyproject.toml`'s `[semantic]` extra) is Phase 4 and should only be built if Phase 1–2 proves insufficient — it is heavy and the user is on limited hardware.

---

## Root Cause Analysis

**Root cause 1 — Vocabulary disjunction (primary, score-determining)**

The actual resume in `data/resume.txt` uses retail vocabulary exclusively: "stocking shelves", "cash handling", "customer service", "fast-paced retail environment". After spaCy lemmatization and stopword removal in `preprocess()`, the resulting token set is roughly `{stock, shelf, cash, handle, customer, service, teamwork, supervise, child, conflict, resolve, first, aid, cpr, soccer, tutor}`.

The target jobs (biology lab assistant, hospital volunteer, research assistant) use vocabulary like `{biology, laboratory, cell, assay, pcr, clinical, patient, research, experiment, specimen, biochemistry}`. The intersection of these two sets is empty. TF-IDF cosine similarity of two vectors that share no non-zero dimensions is mathematically exactly 0, regardless of `ngram_range`, `sublinear_tf`, or any other vectorizer parameter.

**Root cause 2 — Focus boost is post-hoc: it adds to near-zero, not to signal**

The existing focus boost in `TfidfMatcher.score_jobs()` fires when a focus keyword appears in `job.title + " " + job.description`. This is correct design, but it adds to the base TF-IDF score additively. If the TF-IDF base is 0.05 and the boost is 0.15, the result is 0.20 — still below the 0.30 threshold. The boost cannot compensate for a near-zero base score; it can only amplify an existing signal. It also does not help TF-IDF find any overlap — the corpus vectorization has already happened before the boost is applied.

Additionally, `sum(1 for kw in keywords_lower if kw in text)` is an exact substring match. The keyword `"biology"` does not match `"biological"`. The keyword `"research assistant"` is a multi-word phrase that must appear verbatim. This limits boost hit counts in practice.

**Root cause 3 — Threshold calibration is empirically blind**

`match_threshold` was set at 0.35, then lowered to 0.30 in `config.yaml`. The user has no tooling to see the actual score distribution of recent jobs against their resume. Without that visibility, threshold tuning is guesswork. The user may lower threshold to 0.15, start getting notifications for irrelevant jobs, and not understand why.

**Root cause 4 — IDF collapse in a small corpus**

`TfidfVectorizer.fit_transform()` is called on a corpus of `[resume] + [job_1, ..., job_N]` per scan. In a small corpus (say, 20 jobs), IDF weights are unstable. A term appearing in 3 out of 20 documents gets IDF = log(20/3) ≈ 1.9. A term appearing in only the resume gets IDF = log(20/1) ≈ 3.0 — a high IDF weight — but since no jobs contain it, cosine similarity remains 0. IDF cannot bridge vocabulary disjunction; it only reweights terms that already appear in both documents.

---

## Options Compared

| Approach | Solves vocab gap | New deps | Risk | Reversibility | Effort |
|---|---|---|---|---|---|
| **Resume rewrite (user action)** | ✅ Fully | None | Low | N/A | User time |
| **Resume vocabulary expansion (Phase 1)** | ✅ Substantially | None | Low–Medium | Config flag | ~15 lines |
| **Title-priority boost (Phase 2)** | Partially | None | Very low | Config param | ~8 lines |
| Lower threshold to 0.15 alone | ❌ | None | High (spam) | Config change | 0 lines |
| TF-IDF param tuning (ngrams, min_df) | ❌ | None | Low | Config | ~5 lines |
| Hybrid TF-IDF + separate keyword scorer | Partially | None | Medium | Moderate | ~60 lines |
| **SemanticMatcher / sentence-transformers (Phase 4)** | ✅ Fully | `[semantic]` extra | Medium (model DL) | Config flag | ~50 lines |

**Why not threshold lowering alone:** A threshold of 0.15 would fire on customer-service jobs scoring 0.15 against the resume. The user would get notifications for grocery store cashier jobs, which is the opposite of what they want. Threshold tuning is a calibration tool, not a vocabulary solution.

**Why not TF-IDF param tuning:** `ngram_range=(1,2)` and `sublinear_tf=True` are already near-optimal settings. Widening to `(1,3)` adds trigrams that still won't overlap. `min_df=1` is already the most permissive setting. There is no parameter that creates vocabulary overlap where none exists.

**Why not hybrid keyword scorer instead of expansion:** A parallel keyword feature scorer (score_2 = fraction of focus keywords present in job) combined with a weighted sum introduces a new config dimension (alpha weight), a new code path, and a new failure mode. Resume expansion achieves the same practical effect — making the TF-IDF find overlap with biology vocabulary — in a simpler, more maintainable way with no new API surface.

**Why resume expansion works mathematically:** The `TfidfVectorizer` computes cosine similarity between the resume vector and each job vector. If the expanded resume contains `["biology", "laboratory", "clinical", "research"] × 3`, its TF-IDF vector now has non-zero dimensions for those terms. When a job posting also contains "biology laboratory research," both vectors share non-zero dimensions and cosine similarity is > 0. `sublinear_tf=True` means TF(count=3) = 1 + log(3) ≈ 2.1 vs TF(count=1) = 1.0, so the repeated terms have reasonably elevated weight without dominating.

**Why not semantic matcher first:** sentence-transformers requires ~90MB model download, ~500MB PyTorch transitive dependency install, and slower first-run latency. It IS the better long-term answer and is already planned. But it is appropriate only after Phase 1 has been validated and proven insufficient — otherwise we add heavy deps without knowing if the simpler fix would have worked.

---

## Recommended Solution

**Phase 1 — Resume vocabulary expansion:** Add two fields to `FocusConfig`: `expand_resume: bool = False` and `expand_weight: int = 3`. In `TfidfMatcher.score_jobs()`, if `expand_resume` is True, construct `scoring_resume = resume_text + " " + " ".join(focus.keywords * expand_weight)` before calling `preprocess()`. The expansion uses the already-curated `focus.keywords` list, so no new config section is needed. Set `expand_resume: true` in `config.yaml`.

**Phase 2 — Title-priority boost:** Add `title_boost_multiplier: float = 2.0` to `FocusConfig`. In the focus boost loop in `score_jobs()`, check if the keyword appears in `job.title.lower()` specifically (not just the combined text). If so, multiply `boost_per_hit` by `title_boost_multiplier`. This rewards jobs that are fundamentally about the user's interest area (title match) more than jobs that merely mention it in passing (description match).

**Phase 3 — Score diagnostic CLI command:** Add `--diagnose` flag. When invoked, it loads the 50 most-recent jobs from the DB, scores them against the resume using the current matcher, and prints a `rich` table showing rank, title, company, source, base score, boost amount, and final score. Also prints threshold reference line. This is the missing observability tool that lets the user calibrate empirically.

**Phase 4 — SemanticMatcher (conditional):** Implement the already-planned `SemanticMatcher` class behind `use_semantic_matcher: true`. Build only if after Phase 1–2 the user reports that appropriate jobs still score below 0.25. The architecture already has the hook (`build_matcher()` TODO, `use_semantic_matcher` field in `Settings`, `[semantic]` optional dep group).

---

## Step-by-Step Execution Plan

---

### Phase 1 — Resume Vocabulary Expansion

**Goal:** Make TF-IDF find vocabulary overlap between the user's resume and target job postings, by optionally appending focus keywords to the resume text at score time.

**Files affected:**
- `src/localjobscout/config.py` — add two fields to `FocusConfig`
- `src/localjobscout/matcher.py` — use expansion in `score_jobs()`
- `config.yaml` — enable expansion with recommended defaults
- `tests/test_matcher.py` — add 4 new tests

**What changes:**

In `config.py`, `FocusConfig` gains:
```python
expand_resume: bool = False
expand_weight: int = 3
```

In `matcher.py`, `TfidfMatcher.score_jobs()`, before the line `resume_pp = preprocess(resume_text, self._nlp)`:
```python
scoring_resume = resume_text
if self._focus.expand_resume and self._focus.keywords:
    expansion = " ".join(self._focus.keywords * self._focus.expand_weight)
    scoring_resume = resume_text + " " + expansion
resume_pp = preprocess(scoring_resume, self._nlp)
```
The variable `resume_text` is never mutated; `scoring_resume` is local to `score_jobs()`.

In `config.yaml`, under the `focus:` block:
```yaml
focus:
  expand_resume: true
  expand_weight: 3
  keywords: [...]  # existing list, unchanged
```

**Risks:**
- False positives: jobs mentioning any focus keyword now score higher. Mitigated by: (a) the expansion adds signal proportional to full TF-IDF cosine, not a flat boost; (b) `expand_weight: 3` is moderate; (c) the user can tune down to `expand_weight: 1` if needed.
- Existing tests: `test_matcher.py` uses `TfidfMatcher(nlp)` with default `FocusConfig()` where `expand_resume=False`. All 12 existing matcher tests are unaffected.
- mypy: two new `bool` and `int` fields on a Pydantic model are trivially typed. No overrides needed.

**Test strategy (4 new tests in `test_matcher.py`):**
1. `test_expand_resume_increases_biology_job_score` — a biology job scores higher with `expand_resume=True` and biology keywords than with `expand_resume=False`
2. `test_expand_resume_disabled_by_default` — `FocusConfig()` has `expand_resume=False`, scores unchanged vs base `TfidfMatcher`
3. `test_expand_weight_zero_is_no_op` — `expand_weight=0` produces same scores as `expand_resume=False`
4. `test_expand_resume_does_not_inflate_unrelated_job` — a completely unrelated job (e.g., plumber) does not get a meaningfully higher score from biology keyword expansion

**Rollback strategy:** Set `expand_resume: false` in `config.yaml`. One line change. No code change required. The feature is inert without the config flag.

**Success criteria:** A job titled "Research Assistant — Biology Lab, University of Guelph" with description containing "biology", "laboratory", "research" should score ≥ 0.25 after expansion with the existing focus keywords. Verify with `--diagnose` (Phase 3) or a manual test run.

---

### Phase 2 — Title-Priority Boost

**Goal:** Reward jobs where a focus keyword appears in the job *title* more than jobs where it only appears buried in the description body.

**Files affected:**
- `src/localjobscout/config.py` — add one field to `FocusConfig`
- `src/localjobscout/matcher.py` — split title vs. description boost logic
- `tests/test_matcher.py` — add 3 new tests

**What changes:**

In `config.py`, `FocusConfig` gains:
```python
title_boost_multiplier: float = 2.0
```

In `matcher.py`, the focus boost loop currently does:
```python
text = (job.title + " " + job.description).lower()
hits = sum(1 for kw in keywords_lower if kw in text)
boost = min(hits * self._focus.boost_per_hit, self._focus.max_boost)
```

Replace with:
```python
title_lower = job.title.lower()
desc_lower = job.description.lower()
hits = 0.0
for kw in keywords_lower:
    if kw in title_lower:
        hits += self._focus.title_boost_multiplier
    elif kw in desc_lower:
        hits += 1.0
boost = min(hits * self._focus.boost_per_hit, self._focus.max_boost)
```

Note: `hits` changes from `int` to `float` here. mypy will require the type annotation to be `float` from the start. Ensure `hits: float = 0.0` is explicit.

**Risks:**
- The `hits` variable type changes from `int` to `float`. This is safe — it's only used in arithmetic — but mypy will enforce the annotation.
- `title_boost_multiplier: float = 2.0` default is conservative. If a job title says "Medical Receptionist" (contains "medical"), it gets 2x boost per keyword. This is correct behavior.
- Existing tests: `test_focus_boost_increases_score` uses a job titled "Python Clinical" — the keyword "clinical" IS in the title, so the boosted score will increase further. The test asserts `boosted_score > base_score`, which still holds. `test_focus_boost_capped_at_max_boost` checks `boosted <= base + 0.2 + 1e-9` — this still holds since max_boost caps the total.

**Test strategy (3 new tests):**
1. `test_title_match_scores_higher_than_description_match` — two identical jobs except one has keyword in title and one in description; title job scores higher
2. `test_title_boost_multiplier_one_is_neutral` — `title_boost_multiplier=1.0` produces same result as original logic
3. `test_title_boost_capped_by_max_boost` — confirm max_boost still applies when title multiplier inflates hits

**Rollback strategy:** Set `title_boost_multiplier: 1.0` in `config.yaml`. The multiplier is 1.0 = identical to original behavior.

**Success criteria:** "Research Assistant" (keyword in title) should score noticeably higher than "Laboratory Technician — Experience in research preferred" (keyword in description only).

---

### Phase 3 — Score Diagnostic CLI Command

**Goal:** Give the user observability into score distribution so they can tune `match_threshold` empirically rather than blindly.

**Files affected:**
- `src/localjobscout/__main__.py` — add `--diagnose` flag and handler
- `src/localjobscout/db.py` — add `get_recent_jobs(limit: int)` query function
- `tests/test_matcher.py` — no changes needed (diagnostic is a thin wrapper)
- `tests/test_db.py` — add 1 test for `get_recent_jobs()`

**What changes:**

In `db.py`, add one function:
```python
def get_recent_jobs(limit: int = 50) -> list[Job]:
    """Return the most recently seen jobs regardless of score or notified state."""
    ...  # SELECT * FROM jobs ORDER BY first_seen DESC LIMIT ?
```

In `__main__.py`, add a `--diagnose` argument to the argparse setup. Its handler:
1. Calls `Settings.load()`
2. Calls `db.init_db()` and `db.get_recent_jobs(limit=50)`
3. Loads resume and NLP model
4. Calls `matcher.score_jobs()` on all 50 jobs
5. Uses `rich.table.Table` to print columns: Rank, Score, Threshold✓, Title (truncated to 40 chars), Company, Source
6. Prints a summary line: `Top score: X.XX | P90: X.XX | P50: X.XX | Threshold: X.XX`
7. If no jobs in DB, prints a helpful message: "No jobs in database yet — run --once first."

**Risks:**
- `get_recent_jobs()` is a new read-only DB function. No schema changes. Zero risk.
- The diagnostic re-scores all jobs against the current resume, which may differ from the stored scores (stored scores reflect the resume at the time the job was first seen). This is documented in the output header with a note: "Scores below are freshly computed against current resume and config."
- `rich` is already a dependency. No new imports.

**Test strategy (1 new test in `test_db.py`):**
1. `test_get_recent_jobs_returns_most_recent` — insert 5 jobs with different `first_seen` timestamps, verify `get_recent_jobs(limit=3)` returns the 3 most recent in descending order

**Rollback strategy:** The diagnostic command is read-only. There is nothing to roll back. It can be removed later if desired, but it does not affect production behavior.

**Success criteria:** `localjobscout --diagnose` prints a score table without errors when at least one job is in the database. The threshold line visually separates jobs above and below it.

---

### Phase 4 — SemanticMatcher (Build Only If Needed)

**Goal:** Replace TF-IDF with sentence-level embedding cosine similarity using a local model, handling vocabulary mismatch and paraphrase automatically.

**Build condition:** Only proceed if, after Phases 1–3, the user runs `--diagnose` and reports that appropriate jobs (biology lab, research assistant, hospital volunteer) are still scoring below 0.25. If Phase 1 expansion + Phase 2 title boost push those jobs to 0.30+, Phase 4 is unnecessary.

**Files affected:**
- `src/localjobscout/matcher.py` — add `SemanticMatcher` class; update `build_matcher()`
- `src/localjobscout/resume.py` — add `@lru_cache`'d model loader function
- `pyproject.toml` — `[semantic]` extra already exists; may need version pin update
- `config.py` — `use_semantic_matcher: bool = False` already exists; no change needed
- `tests/test_matcher.py` — add 3 new tests, guarded by `pytest.importorskip("sentence_transformers")`

**What changes:**

In `resume.py`, add:
```python
@lru_cache(maxsize=1)
def get_sentence_model() -> Any:  # SentenceTransformer
    sentence_transformers = importlib.import_module("sentence_transformers")
    return sentence_transformers.SentenceTransformer("all-MiniLM-L6-v2")
```

In `matcher.py`, add `SemanticMatcher` class implementing the `Matcher` Protocol:
```python
class SemanticMatcher:
    def score_jobs(self, resume_text: str, jobs: list[Job]) -> list[tuple[Job, float]]:
        # encode resume + all job descriptions in a single batch call
        # compute cosine similarity of resume embedding vs. each job embedding
        # apply focus boost (same logic as TfidfMatcher)
        ...
```

In `build_matcher()`, replace the TODO comment:
```python
def build_matcher(settings: Settings, nlp: Language) -> Matcher:
    if settings.use_semantic_matcher:
        try:
            return SemanticMatcher(focus=settings.focus)
        except ImportError:
            log.warning("sentence-transformers not installed; falling back to TF-IDF")
    return TfidfMatcher(nlp, focus=settings.focus)
```

**Risks:**
- Model download (~90MB `all-MiniLM-L6-v2`) happens on first call. If network is unavailable, it will fail loudly. Mitigated by the ImportError fallback.
- First-run latency: model encoding is slower than TF-IDF on CPU. For 50 jobs, encoding takes ~1–2 seconds on modern hardware. Acceptable.
- mypy: `sentence_transformers` has no `py.typed`. Requires a new `[[tool.mypy.overrides]]` block.
- The `Matcher` Protocol is satisfied because `SemanticMatcher` has `score_jobs(self, resume_text: str, jobs: list[Job]) -> list[tuple[Job, float]]`. Structural subtyping works.

**Rollback strategy:** Set `use_semantic_matcher: false` in `config.yaml`. The `SemanticMatcher` class is never instantiated.

**Success criteria:** With `use_semantic_matcher: true`, a job described entirely in paraphrase of the user's bio background (e.g., "Seeking a motivated science student to assist with bench work") scores ≥ 0.45.

---

## Cursor Implementation Tasks

Each task is self-contained, can be verified independently, and should be executed in order. Do not start the next task until the quality gate passes (pytest, mypy, ruff).

---

**Task 1 — Add expansion fields to `FocusConfig`**
File: `src/localjobscout/config.py`
Add two fields to `FocusConfig` after `max_boost`:
```python
expand_resume: bool = False
expand_weight: int = 3
```
Verify: `python -c "from localjobscout.config import FocusConfig; f = FocusConfig(); assert f.expand_resume is False; assert f.expand_weight == 3"`. mypy + ruff.

---

**Task 2 — Implement resume expansion in `TfidfMatcher.score_jobs()`**
File: `src/localjobscout/matcher.py`
Before the line `resume_pp = preprocess(resume_text, self._nlp)`, insert:
```python
scoring_resume = resume_text
if self._focus.expand_resume and self._focus.keywords:
    expansion = " ".join(self._focus.keywords * self._focus.expand_weight)
    scoring_resume = resume_text + " " + expansion
```
Change `preprocess(resume_text, self._nlp)` to `preprocess(scoring_resume, self._nlp)`.
The variable `resume_text` must NOT be mutated — use the `scoring_resume` local variable only.
Verify: existing 12 tests in `test_matcher.py` still pass (all use `FocusConfig()` with `expand_resume=False`).

---

**Task 3 — Write 4 new tests for resume expansion**
File: `tests/test_matcher.py`
Add a `BIOLOGY_JOB` fixture (a job with "biology laboratory research clinical" in the description) near the existing fixtures. Add these four tests:
1. `test_expand_resume_increases_biology_job_score` — use `FocusConfig(keywords=["biology","laboratory","research","clinical"], expand_resume=True, expand_weight=3)`, assert expanded score > unexpanded score for `BIOLOGY_JOB`
2. `test_expand_resume_disabled_by_default` — `FocusConfig()` produces same score as `TfidfMatcher(nlp)` default for `BIOLOGY_JOB`
3. `test_expand_weight_zero_is_no_op` — `expand_weight=0` produces same score as `expand_resume=False`
4. `test_expand_resume_does_not_inflate_unrelated_job` — create a plumbing/HVAC job, verify its score with expansion is < 0.2 (it contains none of the focus keywords so expansion shouldn't help it much; the TF-IDF cosine of biology keywords vs. plumbing job is still low)
Verify: `pytest tests/test_matcher.py -v`, all 16 tests pass.

---

**Task 4 — Update `config.yaml`**
File: `config.yaml`
Under the `focus:` section, add:
```yaml
expand_resume: true
expand_weight: 3
```
No changes to the `keywords:` list. Consider expanding the `keywords:` list with additional premed-relevant terms: `"pcr"`, `"cell culture"`, `"anatomy"`, `"physiology"`, `"healthcare"`, `"pharmacy"`, `"scribe"`, `"orderly"` to improve coverage.
Verify: `python -c "from localjobscout.config import Settings; s = Settings.load(); assert s.focus.expand_resume is True"`.

---

**Task 5 — Add `title_boost_multiplier` to `FocusConfig`**
File: `src/localjobscout/config.py`
Add to `FocusConfig`:
```python
title_boost_multiplier: float = 2.0
```
Verify: mypy + ruff clean. Existing tests unaffected (default value is 2.0 but code not yet changed).

---

**Task 6 — Implement title-priority boost in `TfidfMatcher.score_jobs()`**
File: `src/localjobscout/matcher.py`
In the focus boost loop, replace the existing `hits` calculation:
```python
# OLD
text = (job.title + " " + job.description).lower()
hits = sum(1 for kw in keywords_lower if kw in text)

# NEW
title_lower = job.title.lower()
desc_lower = job.description.lower()
hits: float = 0.0
for kw in keywords_lower:
    if kw in title_lower:
        hits += self._focus.title_boost_multiplier
    elif kw in desc_lower:
        hits += 1.0
```
The `hits` variable annotation must be explicit `float` to satisfy mypy strict.
Verify: all 16 matcher tests pass. Pay special attention to `test_focus_boost_capped_at_max_boost` — it should still pass because max_boost still caps the total.

---

**Task 7 — Write 3 new tests for title-priority boost**
File: `tests/test_matcher.py`
1. `test_title_match_scores_higher_than_description_match` — create `JOB_KEYWORD_IN_TITLE` with focus keyword in title and different description, and `JOB_KEYWORD_IN_DESC` with same keyword only in description, same resume. Assert title job boost > desc job boost.
2. `test_title_boost_multiplier_one_equals_old_behavior` — with `title_boost_multiplier=1.0`, title match and description match produce equal boost contributions per keyword.
3. `test_title_boost_still_capped_by_max_boost` — even with `title_boost_multiplier=5.0`, total boost cannot exceed `max_boost`.
Verify: all 19 matcher tests pass. mypy + ruff clean.

---

**Task 8 — Add `get_recent_jobs()` to `db.py`**
File: `src/localjobscout/db.py`
Add after `get_unnotified_above()`:
```python
def get_recent_jobs(limit: int = 50) -> list[Job]:
    """Return the most recently first_seen jobs, regardless of score or notified status."""
    db_path = _require_db()
    with _get_conn(db_path) as conn:
        cursor = conn.execute(
            "SELECT * FROM jobs ORDER BY first_seen DESC LIMIT ?",
            (limit,),
        )
        rows = cursor.fetchall()
    return [_row_to_job(row) for row in rows]
```
Verify: mypy + ruff. No new schema or migration required.

---

**Task 9 — Write 1 new test for `get_recent_jobs()`**
File: `tests/test_db.py`
Add `test_get_recent_jobs_returns_most_recent_limit_respected` — insert 5 jobs with incrementing `first_seen` timestamps, call `get_recent_jobs(limit=3)`, assert exactly 3 returned and they are the 3 most recent in descending order.
Verify: all db tests pass.

---

**Task 10 — Implement `--diagnose` in `__main__.py`**
File: `src/localjobscout/__main__.py`
Add `--diagnose` argument to the argument parser. When invoked:
1. Load settings from config.yaml
2. Call `db.init_db(settings.db_path)`; call `db.get_recent_jobs(limit=50)`; if empty, print message and exit
3. Load resume text (`resume.load_resume(settings.resume_path)`) and NLP (`resume.get_nlp()`)
4. Build matcher (`matcher.build_matcher(settings, nlp)`)
5. Call `score_jobs(resume_text, jobs)` — returns `list[tuple[Job, float]]`
6. Print header with `rich.console.Console` and `rich.table.Table` columns: `#`, `Score`, `✓` (checkmark if score ≥ threshold), `Title`, `Company`, `Source`
7. After table, print: `Threshold: {threshold:.2f} | Top: {max_score:.2f} | P90: {p90:.2f} | P50: {p50:.2f}`
8. Calculate percentiles using `sorted(scores)` and index arithmetic — no numpy needed
Verify: `localjobscout --diagnose` runs without error (may show empty DB message if no jobs yet).

---

**Task 11 — Quality gate for Phase 1–3**
Run the full test suite + type checker + linter. Command sequence:
```
pytest
mypy --strict src/
ruff check src/ tests/
```
All 151 + new tests pass. Zero mypy errors. Zero ruff warnings. This is the greenlight gate before any further work.

---

**Task 12 — (Phase 4, conditional) Add `SemanticMatcher`**
Only execute if: after running `--diagnose` against a real job DB, the user confirms that biology/premed jobs are still scoring below 0.25 after Phases 1–3.

File: `src/localjobscout/matcher.py`
Add `SemanticMatcher` class implementing `Matcher` Protocol. Use `sentence_transformers.SentenceTransformer("all-MiniLM-L6-v2")` (lazy-loaded, `lru_cache`'d in `resume.py`). Apply same focus boost logic after encoding. Update `build_matcher()` to branch on `settings.use_semantic_matcher` with ImportError fallback.

File: `pyproject.toml`
Add mypy override for `sentence_transformers.*`.

File: `config.yaml`
Document `use_semantic_matcher: false` with a comment explaining the tradeoff.

Verify: new tests guarded by `pytest.importorskip("sentence_transformers")`. All existing tests unaffected. `use_semantic_matcher: false` (default) produces identical behavior to before.

---

## One Thing Before Cursor Starts

Before beginning Task 1: back up `config.yaml` and `data/jobs.db` (if it exists). The config change in Task 4 affects live behavior. The DB backup is a precaution against anything unexpected:

```
cp config.yaml config.yaml.backup
cp data/jobs.db data/jobs.db.backup   # if it exists
```

This is consistent with the schema migration precaution already documented in the handoff doc.