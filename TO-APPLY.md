# ⏳ APPLY TO THESE NEXT — saved 2026-06-09

**Taha: you marked these but hadn't applied yet (PC restart). Apply, then mark them.**

Recall them anytime:
```
python -m localjobscout --manual-queue --status interested
```

## The 5 jobs (all premed, commutable, were live)

| # | Score | Job | Where | Link |
|---|-------|-----|-------|------|
| 1 | 0.37 | Pharmacy Student or Intern, Drug Distribution | Cambridge Memorial Hospital | https://encareers-cmh.icims.com/jobs/6758/pharmacy-student-or-intern,-drug-distribution-ptc-2026-6758/job |
| 2 | 0.35 | Perioperative Attendant – Endoscopy | Cambridge Memorial Hospital | https://encareers-cmh.icims.com/jobs/6768/perioperative-attendant---endoscopy-ptc-2026-6768/job |
| 3 | 0.35 | Environmental Service Worker (PT) | Cambridge Memorial Hospital | https://encareers-cmh.icims.com/jobs/6633/environmental-service-worker-pt-2026-6633/job |
| 4 | 0.30 | Environmental Service Worker | Cambridge Memorial Hospital | https://encareers-cmh.icims.com/jobs/6794/environmental-service-worker/job |
| 5 | 0.20 | Pharmacy Technician | Homewood Health (Guelph) | https://ca.indeed.com/viewjob?jk=d5ff2a215c945a1b |

Job-id prefixes (for `--mark applied <id>`): `495ad2db` `acb889a2` `4972fb26` `aa1785ce` `622a619e`

### Before applying — check:
- **#1 Pharmacy Student/Intern** — ❌ SKIP: requires pharmacy-program enrollment (you're bio).
  Now auto-excluded by the qualification gate (2026-06-09).
- **#5 Pharmacy Technician** — ❌ SKIP: Ontario techs are OCP-*registered* (regulated
  profession, needs accredited program + exams). Now auto-excluded too.
- **#2 Perioperative Attendant** — best premed story (OR/procedure exposure), no credential needed.

→ Real list is now #2, #3, #4 only.

### After applying, mark them:
```
python -m localjobscout --mark applied 495ad2db   # repeat per id
```

---
**Status of the tool (all committed, working):** premed relevance gate, credential
filters, liveness verification, Indeed Playwright check, Talent.com + Cambridge
hospital (iCIMS) scrapers. Re-pull a fresh shortlist anytime with
`python -m localjobscout --manual-queue`.
