# ui/ (Prism cockpit)
@../docs/UI_PLAYBOOK.md
@../.planning/phases/02-pipeline-cockpit-ui-on-mock-data/02-CONTEXT.md
@../.planning/phases/02-pipeline-cockpit-ui-on-mock-data/02-UI-SPEC.md
@../docs/CONTRACT.md

Rules:
- Read only fields defined in `prism/contract.py`. `src/api/schema.ts` is generated: regenerate with `npm run api:types` (mock API running), never hand-edit.
- `02-UI-SPEC.md` is the design contract and overrides the Playbook where they differ (type sizes 12/13/14 and a 28px serif page title, no ink-3, red only for a fraud hold).
- Use the frontend-design skill for any new screen.
- If the Playwright MCP is installed, verify every change at 1440x900 and 1280x800 against section 6 of `docs/UI_PLAYBOOK.md`.
- The sim clock is read once from `GET /api/clock` into `src/state/clockStore.ts`. Nothing else may call the clock endpoint (the mock clock is stateless and would snap back to its fixture time).
- Derive every time-dependent value in `src/time/timeview.ts` (pure, no imports). Never use `toLocale*` or local-zone `Date` methods on sim times.
- Render engine strings as React text only. No `dangerouslySetInnerHTML`, no `href` built from engine data, no `VITE_` environment variables.

Requirements: Node 22.18 or newer (the `test` and `smoke` scripts run `.ts` files directly through Node's type stripping).

Run (from the repo root):

```bash
MOCK=1 .venv/bin/python -m uvicorn prism.api.app:app --port 8000   # mock API (Windows: set MOCK=1, use .venv\Scripts\python.exe)
npm --prefix ui run dev                                            # http://localhost:5173, proxies /api to :8000
npm --prefix ui run build && npm --prefix ui test && npm --prefix ui run smoke
```

Notes:
- `openapi-typescript` is not a local dependency: it needs the TypeScript 5 compiler API and this project uses TypeScript 7. `npm run api:types` runs it through `npx` with its own TypeScript 5.
- `PRISM_API` overrides the proxy target (default `http://localhost:8000`). It is only for exercising error states against a dead or `MOCK=0` backend.
