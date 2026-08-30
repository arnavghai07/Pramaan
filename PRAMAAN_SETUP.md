# PRAMAAN — Setup & Run Guide

This document gets a developer who has never seen this repository from a
fresh Windows machine to a running PRAMAAN instance. It describes the
**current, frozen implementation** as found in this repository — not the
aspirational full build described in `BUILD_PLAN.md` Part Two (database,
auth, PDF reports, etc.), which is not implemented yet.

For the standing engineering rules that govern this codebase, read
`CLAUDE.md` first. For the phased build history and what is/isn't done,
read `BUILD_PLAN.md`.

---

## 1. What PRAMAAN is

**PRAMAAN** is a prototype compliance checker for packaged-commodity labels
under India's Legal Metrology (Packaged Commodities) Rules, 2011. Team
MetaVision, Smart India Hackathon 2026, problem statement SIH26034.

Most label scanners only check *presence* — is the MRP printed somewhere on
the pack? PRAMAAN also checks *conformity* — is it printed in the character
size the law actually requires (Rule 7)? A pack can have every declaration
present and still be illegal because the print is too small to read without
a ruler.

The system has two independent checks that are combined into one verdict:

- **Rule 6 — declaration extraction.** A vision-language model (VLM) reads
  the label photo and extracts structured fields: manufacturer, address, net
  quantity, MRP, dates, FSSAI licence, etc. (`engine/vlm_extract.py`). The
  model's only job is to report what it sees. It never issues a compliance
  verdict — a deterministic rule engine does that, cross-checking fields
  against each other (MRP vs. unit price, mfg date vs. use-by date, etc.).

- **Rule 7 — physical measurement.** A second photo shows a printed ArUco
  marker of known size next to the MRP declaration. OpenCV detects the
  marker, establishes a pixel-to-millimetre scale from it, and measures the
  actual printed character height (`engine/measure_chart.py`). That
  measurement is compared against the legal minimum from Rule 7's Table I.
  No model or human guess is involved in the measurement itself — it is a
  geometric computation on pixels.

- **Deterministic verdict.** `engine/verdict.py` combines the Rule 6 and
  Rule 7 outcomes into one of three states — `COMPLIANT`, `NON_COMPLIANT`,
  `NEEDS_MANUAL_REVIEW` — using fixed precedence rules, never a model call.
  Anything ambiguous (unparseable data, a measurement within ±0.2 mm of the
  legal threshold, a marker the CV pipeline could not calibrate) is routed
  to `NEEDS_MANUAL_REVIEW` rather than guessed at. This human-in-the-loop
  fallback is deliberate: an automated tool that is confidently wrong is
  worse than one that admits it isn't sure.

## 2. Architecture

```
Browser (getUserMedia camera / file upload)
  |
  v
Next.js 15 frontend  (web/)  — port 3000
  |  HTTP (fetch, multipart form-data)
  v
FastAPI backend  (api/)  — port 8000
  |
  |-- engine/vlm_extract.py  --> Ollama (local VLM: qwen2.5vl:7b)   [Rule 6]
  |-- engine/measure_chart.py --> OpenCV (ArUco marker, homography) [Rule 7]
  |-- engine/verdict.py       --> deterministic combination logic
  |
  v
Evidence overlay image + verdict + findings
  |
  v
Browser (results screen)
```

- **Next.js frontend** (`web/`) — single-page state machine
  (`web/app/page.tsx`) that walks the inspector through: capture/upload the
  label photo → (optionally) capture/upload the Rule 7 marker photo → pick
  or draw the MRP region → view the combined verdict with evidence overlay.
- **FastAPI backend** (`api/main.py`, `api/models.py`) — a thin HTTP layer
  over the vision engine. It saves each upload to a temp file, calls the
  relevant engine function, maps engine exceptions to readable JSON error
  responses, and deletes the temp file. It holds no business logic of its
  own.
- **Ollama / VLM** — runs locally, no cloud call, no API key. Reads the
  label image and returns structured JSON fields per a fixed schema.
- **OpenCV / ArUco measurement** — detects the printed marker, rectifies
  the image, and measures text row heights in millimetres. Includes a
  marker tilt-check that rejects unreliable measurements outright rather
  than silently returning a wrong number.
- **Deterministic rules engine** — the only code path allowed to produce a
  compliance verdict, per `CLAUDE.md` rule 1 ("the model never decides").

## 3. System requirements

These are the versions actually observed in this repository's development
environment — the repository does not pin a Python or Node version itself,
so treat these as "known to work," not a hard floor unless stated:

| Requirement | Version used in this repo | Notes |
|---|---|---|
| OS | Windows | Development environment. No macOS/Linux-specific code is used (pure Python + Next.js), but this has only been verified on Windows. |
| Python | 3.13.7 | No `.python-version` or `pyproject.toml` pins a version — this is simply what the existing `.venv` was built with. |
| Node.js | v24.20.0 | No `.nvmrc`/`engines` field in `web/package.json` pins a version. |
| npm | 11.19.0 | Ships with the Node version above. |
| Git | Any recent version | For cloning/branching only. |
| Ollama | Latest | Must be installed and running locally — see below. |
| Ollama model | `qwen2.5vl:7b` | The only model the backend defaults to (`api/main.py`'s `DEFAULT_MODEL`, and `.env.example`'s `VLM_MODEL`). A smaller `qwen2.5vl:3b` was evaluated as an emergency-only fallback but is **not** the default anywhere in the code — do not switch to it unless the 7B model is unusable on the demo machine. |
| RAM / CPU | Not documented in the repo | CPU-only VLM inference is the assumed deployment (`BUILD_PLAN.md`'s "Deploy" row: "Docker Compose, CPU-only inference"). No specific RAM/CPU minimum is established by testing recorded in this repo. |
| Disk | Not documented in the repo | The Ollama model pull itself is several GB; beyond that, no specific disk requirement is recorded. |

**CPU inference is slow — this is expected, not a bug.** `BUILD_PLAN.md`
records real measurements in this environment: a full Rule 6 scan took as
long as ~217 seconds, and a text-only health check (no image) took ~26
seconds including a cold model reload. Budget several minutes for a first
scan, and consider running one "warm-up" scan before a live demo so the
model is already loaded in memory.

## 4. Fresh installation

### 4.1 Clone the repository

```powershell
git clone <repository-url> pramaan
cd pramaan
```

(Replace `<repository-url>` with this repository's actual clone URL — it is
not recorded in-repo, since a repo does not reference its own remote URL.)

### 4.2 Install Ollama and pull the model

Install Ollama from its official Windows installer, then, with the Ollama
app/service running:

```powershell
ollama pull qwen2.5vl:7b
```

This is the exact model name the backend expects
(`api/main.py`: `DEFAULT_MODEL = os.environ.get("VLM_MODEL", "qwen2.5vl:7b")`,
and `.env.example`: `VLM_MODEL=qwen2.5vl:7b`). Ollama listens on
`http://localhost:11434` by default; the backend expects it there unless
`OLLAMA_HOST` is overridden (see 4.3).

### 4.3 Python backend environment

From the repository root:

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

`requirements.txt` pins: `fastapi`, `uvicorn`, `pydantic`, `numpy`,
`opencv-python`, `requests`, `python-multipart`, and their transitive
dependencies. There is no separate `dev-requirements.txt`.

**Environment variables — important discrepancy to be aware of:** the repo
ships `.env.example` documenting `OLLAMA_HOST` and `VLM_MODEL`, but no code
in `api/` or `engine/` loads a `.env` file (no `python-dotenv` import
anywhere, and it is not in `requirements.txt`). Copying `.env.example` to
`.env` alone has **no effect** — the values are read via
`os.environ.get(...)` with hardcoded defaults that already match
`.env.example` (`http://localhost:11434` and `qwen2.5vl:7b`). If you need a
different Ollama host or model, set the real environment variable in the
shell you launch `uvicorn` from, e.g.:

```powershell
$env:OLLAMA_HOST = "http://localhost:11434"
$env:VLM_MODEL = "qwen2.5vl:7b"
```

For the default setup (Ollama on its standard local port, `qwen2.5vl:7b`
pulled), you can skip this entirely — the built-in defaults already match.

### 4.4 Frontend environment

```powershell
cd web
npm install
```

`web/.env.local.example` documents one variable:

```
NEXT_PUBLIC_API_URL=http://localhost:8000
```

Unlike the backend's `.env.example`, Next.js **does** natively load
`.env.local` at build/dev time, so this one is real and should be copied:

```powershell
copy .env.local.example .env.local
```

If skipped, `web/lib/api.ts` falls back to the same
`http://localhost:8000` default, so this step is optional as long as the
backend runs on port 8000.

**Port coupling — not configurable via a single flag:** the backend's CORS
policy (`api/main.py`) explicitly allows only `http://localhost:3000` as an
origin. If you run the frontend on a different port, API calls will be
blocked by the browser even though the server itself returns 200 (this bit
the team once during development — see `BUILD_PLAN.md` Phase B). Keep the
frontend on port 3000 unless you also edit `allow_origins` in
`api/main.py`.

## 5. Running PRAMAAN

Two terminals, both from the repository root.

**Terminal 1 — backend** (with `.venv` activated):

```powershell
uvicorn api.main:app --reload --port 8000
```

**Terminal 2 — frontend:**

```powershell
cd web
npm run dev
```

(`npm run dev` runs `next dev --turbopack` per `web/package.json`.)

Then open **http://localhost:3000** in a browser. Ollama must already be
running in the background (its Windows installer normally registers it as
a background service/tray app — confirm it's running before starting the
backend, since the backend has no retry/wait logic for it).

There is no `/health` endpoint and no Docker Compose setup in this
repository yet (Docker Compose is listed under `BUILD_PLAN.md` Part Two,
Phase J — not implemented). The only way to confirm the backend is up is
to hit a real endpoint or watch the frontend successfully complete a scan.

## 6. Verifying the install — demo images

The repository ships a photo corpus for exactly this purpose:

- **`photos/`** — ten labelled packs used for Rule 6 extraction testing
  (`chilli.jpg`, `creatin.jpg`, `iron.jpg`, `oats.jpg`, `oil.jpg`,
  `pen.jpg`, `perfume.jpg`, `seed.jpg`, `sun.jpg`, `wal.jpg`), plus
  `truth.csv` (recorded expected values for the corpus).
- **Root-level `pt.jpg`** and **`walnutt.jpg`** — real physical-pack
  photos with a genuine printed ArUco marker, used for Rule 7 measurement
  testing (see `BUILD_PLAN.md`'s "Phase C+" section for the exact verified
  results on each).
- **Root-level `truth_template.csv`** — a blank template for recording
  expected values on a new pack, for anyone extending the corpus.

**Known-safe packs to demo** (per `BUILD_PLAN.md`'s explicit list):
`oil.jpg`, `wal.jpg`, `pen.jpg`, `oats.jpg`, `chilli.jpg`. **Do not** use
`seed.jpg`, `iron.jpg`, `perfume.jpg`, `sun.jpg`, or `creatin.jpg` as
showcase examples — each has a documented, still-unfixed extraction bug
(see `CLAUDE.md`'s "Known bugs" section). They are left in the corpus
deliberately, as regression evidence, not as demo material.

A minimal smoke test, once both servers are running:

1. In the browser, upload `photos/wal.jpg` for Rule 6. Expect (after
   CPU-inference latency — see Section 3) 6/6 mandatory declarations
   present, MRP `400.00`.
2. Click "Add Rule 7 measurement (calibrated)" and upload `walnutt.jpg`.
   This pair (`wal.jpg` + `walnutt.jpg`) is the confirmed same-product
   Rule 6 + Rule 7 demo pair; it is documented to resolve to
   `NEEDS_MANUAL_REVIEW` via the manual-region fallback, not an automatic
   `PASS` — this is expected, not a failure of the install.
3. For a clean automatic `PASS`, start a fresh scan and use `pt.jpg` for
   **both** the Rule 6 and Rule 7 steps (it is a single photo that carries
   both the declarations and the marker) — documented to produce
   `COMPLIANT`, Rule 7 measured at 2.35 mm against a 1.5 mm threshold.

You can also drive the engine directly from the command line without the
web UI, to isolate whether an issue is in the engine or in the API/UI
layer:

```powershell
:: single Rule 6 extraction
python engine\vlm_extract.py "photos\oats.jpg" --backend ollama

:: Rule 6 self-test (validation layer only, no model call)
python engine\vlm_extract.py --selftest

:: Rule 7 synthetic self-test (18 checks, no photo needed)
python engine\measure_chart.py --self-test

:: verdict-combination self-test (8 checks, pure logic, no I/O)
python engine\verdict.py --self-test
```

## 7. Repository structure (as it exists today)

```
pramaan/
├── api/                  FastAPI service (main.py, models.py)
├── engine/               Vision engine
│   ├── vlm_extract.py    Rule 6 — VLM-based declaration extraction (live)
│   ├── measure_chart.py  Rule 7 — ArUco measurement + Table-I verdict (live)
│   ├── verdict.py        Deterministic Rule 6 + Rule 7 combination (live)
│   ├── extract.py        Legacy pre-VLM regex/OCR field classifier —
│   │                     superseded by vlm_extract.py, kept for reference
│   ├── batch.py          Legacy batch runner over extract.py's pipeline,
│   │                     not vlm_extract.py's — not part of the live API
│   └── __init__.py
├── web/                  Next.js 15 frontend (App Router, TypeScript, Tailwind)
│   ├── app/page.tsx      Top-level state machine / page
│   ├── components/       CapturePanel, ResultsPanel, Rule7Panel, etc.
│   └── lib/api.ts        Typed fetch wrappers for every backend endpoint
├── photos/               Rule 6 test corpus (10 packs) + truth.csv
├── archive/              Historical/superseded files, kept for provenance,
│                         not referenced by any live code path
├── pt.jpg, walnutt.jpg   Root-level Rule 7 physical-marker demo evidence
├── cm.png                A candidate Rule 7 evidence photo whose marker
│                         could not be detected by any tested ArUco
│                         dictionary — not currently usable as evidence;
│                         left in place pending a decision, not deleted
├── truth_template.csv    Blank template for recording a new pack's truth
├── requirements.txt      Python backend dependencies
├── .env.example          Documents OLLAMA_HOST / VLM_MODEL (see 4.3 caveat)
├── CLAUDE.md              Standing engineering rules for this repo
└── BUILD_PLAN.md          Phased execution plan and current status
```

**Legacy code note:** `engine/extract.py` and `engine/batch.py` predate the
switch to VLM-based extraction (see `vlm_extract.py`'s own docstring:
"Replaces the regex classifier with a vision-language model"). They are
not called by `api/main.py`, `vlm_extract.py`, or `measure_chart.py`. They
remain in the repository for historical reference only — do not treat them
as the current pipeline.

## 8. Known limitations of the current implementation

These are documented gaps, not setup problems — a fresh install behaving
this way is working as intended:

- **No database, no auth, no PDF/DOCX reports, no inspection history.**
  These are Part Two of `BUILD_PLAN.md` (post-5-September scope) and are
  not implemented. The current prototype has no persistence at all —
  every scan is stateless.
- **No automatic Rule 7 candidate-to-MRP matching.** The automatic
  candidate picker detects text-shaped rows on the calibration photo; it
  does not know which row is legally the MRP. The UI shows the Rule 6
  MRP value as a plain-text hint for the inspector to cross-check by eye
  (this is intentionally *not* automated — see `CLAUDE.md` rule 1).
- **No real physical Rule 7 `FAIL` evidence.** Every real physical pack
  tested to date is either compliant or borderline; only a synthetically
  generated fixture has exercised the `FAIL` path. See `BUILD_PLAN.md`'s
  Phase C+ notes for the exact evidence and its caveats.
- **Known Rule 6 extraction bugs remain unfixed** on specific corpus
  photos (`seed.jpg`, `iron.jpg`/`perfume.jpg`/`sun.jpg`,
  `creatin.jpg`) — see `CLAUDE.md`'s "Known bugs" section. These are
  deliberately left as regression evidence, not demo material.

## 9. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Frontend shows "Could not reach the PRAMAAN API… Is uvicorn running?" | Backend not running, or running on a port other than 8000 | Start/restart `uvicorn api.main:app --reload --port 8000` |
| Backend returns a 502 with an Ollama-related message | Ollama not running, or the model isn't pulled | Confirm the Ollama app/service is running; run `ollama pull qwen2.5vl:7b` |
| Browser shows a network/CORS error even though the backend logs `200 OK` | Frontend is running on a port other than 3000 | Run the frontend on port 3000, or edit `allow_origins` in `api/main.py` |
| A scan takes minutes to return | Expected — CPU-only VLM inference | Wait it out; see Section 3. Not a hang. |
| `MarkerNotFound` / `MarkerTilted` error on a Rule 7 photo | No ArUco marker in frame, wrong dictionary, or camera angle too steep | Re-photograph the marker flat, well-lit, filling a reasonable portion of the frame, alongside the MRP text |
| `.env` changes have no effect | Backend does not load `.env` (see Section 4.3) | Set the real environment variable in the shell before starting `uvicorn` |

---

*This document describes the repository as inspected on the date it was
written. If the running application ever disagrees with something stated
here, trust the running application and treat this file as needing an
update, not the other way around.*
