# CLAUDE.md — standing instructions for this repository

Read this file, then `BUILD_PLAN.md`, before starting any task.

## What this project is

**PRAMAAN** — an AI-assisted Legal Metrology compliance engine for packaged
commodities. Team **MetaVision**. Smart India Hackathon 2026, problem statement
**SIH26034** (Ministry of Consumer Affairs, Food & Public Distribution).

Most label scanners check *presence*: is the MRP printed? PRAMAAN checks
*conformity*: is it printed in the manner and character size Rule 7 prescribes?
A pack can carry every declaration and still be illegal because the text is
1.5 mm tall. That gap is the product.

## Deadlines

- **5 September 2026 — working prototype.** This is the live constraint.
- 20 September 2026 — full presentation and submission.

Anything not needed for a three-minute demo on 5 September is deferred. Do not
start deferred work without being asked.

## Non-negotiable rules

These are not style preferences. A change that breaks one of them is rejected.

1. **The model never decides.** The VLM extracts what it sees; a deterministic
   rule engine issues the compliance verdict. No code path may let a model
   output a legal verdict directly.
2. **Silence is never a pass.** If a check cannot run — unparseable date,
   corrupted quantity, missing field — it must emit `REVIEW` or a stated
   problem. A skipped check that prints nothing reads to a user as success.
   This has already caused a real bug (see Known Bugs below).
3. **Confident wrong beats nothing is false.** Any value within ±0.2 mm of a
   Rule 7 threshold, or read at low confidence, returns `REVIEW`, never a
   verdict.
4. **Every extracted field is cross-checked.** MRP must exceed unit sale price;
   manufacture date must precede use-by; MRP ÷ net quantity must reconcile with
   the printed unit price. Each of these checks exists because a real pack broke
   it.
5. **Measurement uses raw camera frames only.** Document-scanner apps rectify to
   a fixed page aspect and scale x and y differently — the image looks perfect
   and every millimetre reading is wrong (measured: 5.8% error on A4, 33% on
   square). The ArUco marker aspect-ratio check must run on every measurement
   shot.
6. **Licensing.** Every dependency must be Apache-2.0, BSD or MIT. Ultralytics
   YOLO is AGPL-3.0 and is explicitly excluded.

## Tech stack

| Layer | Choice |
|---|---|
| Vision (extraction) | Ollama + `qwen2.5vl`, local |
| Vision (measurement) | OpenCV — ArUco, homography, CLAHE |
| Backend | FastAPI (Python), Pydantic models |
| Frontend | Next.js 15 App Router, TypeScript, Tailwind, shadcn/ui |
| Database | PostgreSQL via Supabase (deferred past 5 Sep) |
| Reports | ReportLab (PDF); docxtpl (DOCX, deferred) |

Do not introduce a new framework, ORM or state library without asking first.
Added scope is the main threat to the 5 September date.

## Known bugs — fix before adding features

1. `seed.jpg`: `use_by` extracted as `115 Per g`, a per-100g price string, not a
   date. The mfg→use-by cross-check silently skipped and the run still printed
   "Validation clean". Violates rule 2 above.
2. `iron.jpg`, `perfume.jpg`: `CM/L 0009878017` and `GC/2049` classified as
   `fssai_licence`. `CM/L` is a BIS licence; an iron and a perfume have no FSSAI
   number. Validate format (FSSAI is 14 digits) and gate the field on product
   category.
3. `creatin.jpg`: `net_quantity` reported MISSING while MRP 1299.00 and unit
   price 4.06 imply ~320 g. Back-derive quantity when two of the three values
   are present, and mark it `DERIVED`, not `PRESENT`.

## Working agreement

- **One phase at a time.** Finish and commit the current phase in `BUILD_PLAN.md`
  before opening the next. Tick its checkboxes in the same commit.
- **Plan before writing.** For any task touching more than one file, state the
  plan and wait for approval.
- **Atomic commits**, conventional messages (`feat(api):`, `fix(engine):`,
  `chore(init):`). The repository must build at every commit.
- **Summarise on finish:** files created or changed, commands run, commit hash.
- **Explain the reasoning.** The developer is learning this stack. For each
  non-obvious step, say what it does and why this approach over the alternative.
  Do not just emit code.
- **Never edit or delete photos in `photos/`.** That corpus is evidence; a
  regenerated file invalidates every accuracy number measured against it.

## Commands

```bat
:: activate the environment (Windows cmd, from repo root)
.venv\Scripts\activate

:: single pack extraction
python engine\vlm_extract.py "photos\oats.jpg" --backend ollama

:: whole corpus
for %f in (photos\*.jpg) do python engine\vlm_extract.py "%f" --backend ollama

:: API dev server
uvicorn api.main:app --reload --port 8000

:: frontend dev server
cd web && npm run dev
```

## Environment

Windows, ASUS laptop, Python via `cmd`. OpenCV 5.0.0.93, numpy 2.5.2.
Ollama runs locally — assume no internet at the demo venue.
