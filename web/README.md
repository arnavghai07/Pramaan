# PRAMAAN — web

Capture and results interface for the PRAMAAN compliance scanner. Next.js 15
(App Router), TypeScript, Tailwind, shadcn/ui.

## Dev

```bash
cp .env.local.example .env.local   # NEXT_PUBLIC_API_URL, defaults to localhost:8000
npm install
npm run dev
```

Requires the FastAPI service running (`uvicorn api.main:app --reload --port 8000`
from the repo root) — this app calls `/scan` and has no logic of its own.

See the repo root `CLAUDE.md` and `BUILD_PLAN.md` for project context.
