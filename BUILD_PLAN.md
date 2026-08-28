# PRAMAAN — Master Build Specification & Execution Plan

> **Revised 28 Aug 2026.** Two deadlines now, not one:
> **5 September — working prototype** (the live constraint) and
> **20 September — full presentation**.
> Update checkboxes as you go and commit this file with each phase.

---

## Project Overview

- **Project Name:** PRAMAAN
- **Team:** MetaVision
- **Problem Statement:** SIH26034 — Software system to check compliance of
  packaged commodities under the Legal Metrology (Packaged Commodities) Rules,
  2011 by scanning products, images and labels
- **Ministry:** Ministry of Consumer Affairs, Food & Public Distribution (DoCA)

**Core purpose.** Most label-scanning tools verify *presence*: is the MRP
printed? PRAMAAN verifies *conformity*: is it printed in the manner and size the
Rules prescribe? Rule 7 sets a minimum character height and width-to-height
ratio. A pack can carry every declaration and still be non-compliant because the
text is too small, and an inspector cannot measure a 1.5 mm character by eye.
That gap is the product.

**Target audience.** Legal Metrology inspectors and State Controllers (primary),
DoCA oversight (secondary), manufacturers running pre-market self-audit
(tertiary).

**Key features**

- **Rule 7 measurement engine** — an ArUco fiducial gives a pixel-to-millimetre
  scale from an ordinary photograph, so character height is *measured*, not
  estimated. Validated at 0.05 mm error on rows of 2 mm and above; 0.15 mm at
  the 1 mm legal floor.
- **Rule 6 extraction** — a vision-language model reads the declaration panel
  and returns structured fields, generalising across arbitrary label layouts.
- **Deterministic verdict layer** — the model reports what it sees; a rule
  engine decides compliance. The legal decision is never made by a model.
- **Enforcement console** — inspection repository, search, role-based access,
  hash-chained evidence, PDF/DOCX reports, district dashboards.

---

## Status as of 28 August 2026

**Working:**

- ArUco pixel→millimetre measurement. Day-1 gate passed on a screen chart at
  1.9 MP / 10.7 px per mm: the 1.0 mm row read 1.15 mm; every row at 2.0 mm and
  above landed within 0.05 mm.
- `vlm_extract.py` with the Ollama backend, run across the full 10-photo
  corpus. Eight of ten packs returned 6 of 6 mandatory Rule 6 declarations.
  The model generalised across food, a supplement, cosmetics, a pen and an iron
  without per-layout tuning — the hardest technical risk is retired.

**Not yet true:**

- **Presence accuracy is not value accuracy.** The 8/10 figure counts fields
  that appeared. Several appeared with wrong values (see Known Bugs). There is
  no measurement of value correctness yet, so the real number is unknown.
- Rule 7 has never been run on a physical pack with a printed marker — only on
  a chart displayed on a screen.
- Nothing is wrapped in an API. Engine, interface and reports have never been
  integrated.
- No repository, no version control.

**Known bugs — highest priority, fix before new features:**

| # | Pack | Symptom | Root cause |
|---|---|---|---|
| 1 | `seed.jpg` | `use_by` = `115 Per g` and the run still printed "Validation clean" | Unparseable value silently skipped the mfg→use-by cross-check. Direct violation of the "silence is never a pass" rule. |
| 2 | `iron.jpg`, `perfume.jpg` | `CM/L 0009878017` and `GC/2049` filed as `fssai_licence` | Any licence-shaped string is routed to the FSSAI slot. `CM/L` is a BIS number; neither product has an FSSAI licence. |
| 3 | `creatin.jpg` | `net_quantity` MISSING although MRP 1299.00 ÷ unit price 4.06 ⇒ ~320 g | No back-derivation when two of the three related values are present. |

---

## Tech Stack & Tooling

| Layer | Choice | Why this one |
|---|---|---|
| Frontend | Next.js 15 (App Router), TypeScript, Tailwind, shadcn/ui | One codebase serves the desktop console and the field mobile view via `getUserMedia`. Avoids a second React Native build. |
| Backend | FastAPI (Python) | The vision engine is Python. Same language on both sides removes a serialisation boundary. |
| Database | PostgreSQL (Supabase) + Row-Level Security | RLS enforces officer / supervisor / controller separation *in the database*, so an API bug cannot leak another district's cases. |
| Storage | Supabase Storage (S3-compatible) | Evidence images with signed URLs. |
| Auth | Supabase Auth with role claims | Roles drive RLS policies directly. |
| Data fetching | TanStack Query | Cache invalidation around long-running scan jobs. |
| Vision | Ollama + `qwen2.5vl` (local) | Keeps the "no gated API, runs offline" claim true. Gemini is the documented fallback if local accuracy proves insufficient. |
| CV | OpenCV (ArUco, homography, CLAHE) | Metric measurement. No model can do millimetres. |
| Reports | ReportLab (PDF), docxtpl (DOCX) | The PS requires both formats. |
| Deploy | Docker Compose, CPU-only inference | Deployable on existing government hardware; no licence cost. |

**Licensing constraint:** every dependency must be Apache-2.0, BSD or MIT.
Ultralytics YOLO is AGPL-3.0 and is explicitly excluded.

---

## Architecture & Coding Standards

1. **Clean code and modularity.** TypeScript on the frontend, type hints plus
   Pydantic models on the backend. Atomic components, no god-files.
2. **Error handling.** Zod at the client boundary, Pydantic at the server
   boundary. Every external call (Ollama, storage, database) has an explicit
   failure path.
3. **State tracking.** Finish the current phase before starting the next.

### Project-specific rules, learned the hard way

4. **The model never decides.** A VLM extracts; the rule engine judges. Any
   change that lets a model output a compliance verdict directly is rejected in
   review.
5. **Every extracted field is cross-checked.** MRP must exceed unit sale price;
   manufacture date must precede use-by; MRP ÷ quantity must match the printed
   unit price. These checks exist because real packs broke each of them.
6. **Silence is never a pass.** If a check cannot run — unparseable date,
   corrupted quantity — say so. A skipped check that prints nothing reads as a
   success. Bug 1 above is this rule being violated in production output.
7. **Confident wrong beats nothing is false.** Values near a Rule 7 threshold,
   or read at low confidence, are returned as `REVIEW`, not as a verdict.
8. **Measurement uses raw camera frames only.** Document-scanner apps rectify to
   a fixed page aspect, scaling x and y differently. The image looks perfect and
   every millimetre reading is wrong. Verified: 5.8% error on A4 rectification,
   33% on square. The marker aspect check must run on every measurement shot.

---

## Execution & Git Rules

1. **Repository.** `git init`, `main` branch, Python + Node `.gitignore` before
   the first commit.
2. **Atomic commits per sub-task**, conventional messages, repo buildable at
   every commit.
3. **One phase at a time.** Update this file's checkboxes and include it in that
   phase's commit.
4. **Validate each step:** zero type errors, clean dependency install, new env
   vars documented in `.env.example`.
5. **Summarise and confirm** before moving on: files created, commands run,
   commit hash.

---

# PART ONE — Prototype sprint to 5 September

> **Why the original order changed.** The first plan said build the risky thing
> first, and that was right on 20 August: the vision engine could still have
> failed outright. It didn't. Ten packs came back through a local model with no
> per-layout tuning, so extraction is no longer the open risk.
>
> The open risk now is **integration** — engine, API and interface have never
> spoken to each other, and that is where multi-day surprises live. So the API
> contract comes first, the interface second, and engine accuracy work moves to
> the day when everything is already wired and a fix is cheap to verify.
>
> Everything in Part Two is real and required. It is simply not required by
> 5 September, and starting it early is the most likely way to miss the date.

### Phase A — Repository and API contract (28–29 Aug)

- [x] `git init`, `main` branch, `.gitignore` for Python and Node
- [x] Move existing scripts into `engine/`: `measure_chart.py`, `extract.py`,
      `vlm_extract.py`, `preprocess.py`, `batch.py`, `make_screen_chart.py`
      (`preprocess.py` and `make_screen_chart.py` do not exist in this repo —
      everything that exists has been moved)
- [x] **Re-run the full 10-pack corpus after the move and before the first
      commit.** Moving files breaks relative paths; the first commit must be a
      working tree.
- [x] `.venv` created, `requirements.txt` pinned, `.env.example` with
      `OLLAMA_HOST`, `VLM_MODEL`
- [x] `README.md` stub with the one-paragraph pitch
- [ ] Refactor `vlm_extract.py` so its core is an importable function returning a
      dict — not only a `__main__` that prints a table. The CLI keeps working by
      calling that function.
- [ ] `POST /scan` — image in, structured fields plus verdict out
- [ ] `POST /measure` — image plus marker size in, millimetre measurements out
- [ ] Pydantic request/response models; error middleware mapping every model
      failure to a readable client error
- [ ] **Gate: `/scan` returns the same fields for `oats.jpg` that the CLI does.**
- [ ] **Commit:** `chore(init): repository scaffolding and existing vision engine`
- [ ] **Commit:** `feat(api): FastAPI service wrapping the vision engine`

### Phase B — Capture and results interface (30–31 Aug)

- [ ] Next.js 15 app in `web/`, Tailwind and shadcn/ui configured
- [ ] Capture screen: `getUserMedia` live view, framing guide, file-upload
      fallback (**the fallback is the demo path — never rely on venue lighting
      or a browser camera permission prompt on stage**)
- [ ] Results view: field table with PRESENT / MISSING / REVIEW states, verdict
      banner, mandatory fields visually separated from optional ones
- [ ] Loading skeletons and error states — a scan takes seconds and a frozen
      screen reads as a crash
- [ ] Mobile-responsive layout
- [ ] **Gate: photograph a pack, see a verdict, end to end, in the browser.**
- [ ] **Commit:** `feat(ui): capture and results interface`

### Phase C — Rule 7 measurement in the loop (1–2 Sep)

- [ ] Print ArUco markers; run measurement on a **real pack**, not the screen
      chart — the first time this leaves the lab condition
- [ ] Marker aspect-ratio check rejects rectified images with a clear message
- [ ] Rule 7 Table I numeral heights sourced **from the gazette text, not a
      summary site**, before the rule engine encodes them
- [ ] Values within ±0.2 mm of a threshold return `REVIEW`
- [ ] Evidence overlay: annotated image with each measured character boxed and
      labelled with its height in mm
- [ ] **Gate: a genuinely non-compliant pack produces a correct FAIL with a
      measurement the judges can see on the image.**
- [ ] **Commit:** `feat(engine): Rule 7 measurement on physical packs with evidence overlay`

### Phase D — Report and repository (3 Sep)

- [ ] PDF compliance report (ReportLab): fields, verdict, rule citations,
      annotated evidence image
- [ ] Inspection list view with search; single hardcoded officer role — **no
      auth, no RLS, no Supabase yet**
- [ ] Seed the 10 corpus packs as historical inspections so the list is not empty
      on stage
- [ ] **Commit:** `feat(reports): PDF export and seeded inspection repository`

### Phase E — Accuracy pass (4 Sep)

- [ ] Fix known bugs 1, 2 and 3 (see Status table above)
- [ ] `truth.csv` recording the **correct value** per field, not only presence
- [ ] `batch.py` reports recall, false positives and **value accuracy**
- [ ] Re-run all 10 packs; record the value-accuracy number
- [ ] **Gate: no run may print "Validation clean" while a mandatory field is
      missing or unparseable.**
- [ ] **Commit:** `fix(engine): field classification, back-derivation, value accuracy harness`

### Phase F — Demo readiness (5 Sep)

- [ ] Full build, zero lint or type errors, clean install from `requirements.txt`
      and `package.json` on a fresh clone
- [ ] Demo script timed to 3 minutes, database pre-seeded
- [ ] **Backup video recorded** — assume the live demo fails
- [ ] Offline rehearsal with wifi switched off at the wall
- [ ] `README.md`: setup, local dev, architecture diagram
- [ ] **Commit:** `docs(prototype): demo assets and setup documentation`

---

# PART TWO — Full build to 20 September

> Deferred deliberately, not forgotten. Each item is an explicit functional
> requirement of the problem statement. Most teams polish the scanner and arrive
> with none of them.

### Phase G — Engine hardening (6–9 Sep)

- [ ] Benchmark `qwen2.5vl:3b` against `qwen2.5vl:7b` on the corpus.
      **Gate: if 7B clears the busy labels, the offline claim stands.**
      If it does not, Gemini becomes the documented fallback and slide 4 of the
      deck must be rewritten.
- [ ] Extend the corpus from 10 packs to 25: food, cosmetics, hardware, imported
      goods, and at least 3 deliberately non-compliant samples
- [ ] Multi-shot consensus: 3 photos per pack, highest-confidence field wins
- [ ] **Gate: ≥85% field-level value accuracy**
- [ ] **Commit:** `feat(engine): multi-shot consensus and 25-pack corpus`

### Phase H — Database, auth, audit trail (10–13 Sep)

- [ ] Tables: `inspections`, `declarations`, `violations`, `evidence`, `audit_log`
- [ ] RLS policies for officer / supervisor / controller
- [ ] SHA-256 hash-chained `audit_log`; EXIF geolocation and timestamp bound to
      evidence
- [ ] Migrate the seeded inspections out of the prototype's local store
- [ ] **Commit:** `feat(auth-db): schema, RLS policies, hash-chained audit trail`

### Phase I — Console completion (14–17 Sep)

- [ ] Officer review flow: accept, override, request re-capture
- [ ] DOCX report export (docxtpl) alongside PDF
- [ ] District dashboard: violation rates, top offenders, inspection throughput
- [ ] **Commit:** `feat(console): review workflow, DOCX export, district dashboard`

### Phase J — Release (18–19 Sep)

- [ ] Docker Compose, CPU-only inference, deployable offline
- [ ] Technical documentation of architecture and deployment framework
      (an explicit PS requirement)
- [ ] Final presentation rehearsal and backup video refresh
- [ ] **Commit:** `docs(release): documentation, demo assets, deployment guide`

---

## Open Risks

| Risk | Status | Mitigation |
|---|---|---|
| Value accuracy unmeasured — presence 8/10 hides wrong values | **Open** | Phase E builds the harness and produces the real number |
| 3B model returns empty on busy labels (oats pouch) | Open | Phase G tests 7B; crop to declaration block; Gemini fallback |
| Field classifier routes any licence string to FSSAI | **Open** | Bug 2 — format validation plus product-category gating |
| 1 mm measurement error is 0.15 mm — thin against a 1 mm threshold | Known | Values within ±0.2 mm of a threshold return `REVIEW` |
| Rule 7 measurement never tested on a physical pack | **Open** | Phase C — first real-world test, 1–2 Sep |
| OCR digit confusions differ by engine (`2G0GM` vs `2S0GM`) | Partly fixed | Character repair in numeric contexts; multi-shot consensus in Phase G |
| Document-scanner apps break metric measurement | Fixed | Marker aspect check rejects non-uniformly scaled images |
| Offline claim depends on the local model being good enough | Open | Phase G gate decides |
| Scope creep from Part Two into the sprint | **Live** | Part Two work does not begin before 5 September |

---

## Getting Started

Complete **Phase A**, tick its boxes, commit with the specified messages, then
go to Phase B. Do not open a Part Two item before 5 September.
