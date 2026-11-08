# Prism

**Prism** is team Prism's HackUTD 2026 entry for the Goldman Sachs challenge, also entered in the NVIDIA Nemotron track.

Prism reads a bank's shared loan folder, extracts the deal facts, moves each deal through the lending pipeline
(Intake → Credit → Committee → Product → Committed → Closed) on a simulated clock, asks the right person at every
sign-off, alerts and escalates when a deal is stuck, and catches fraud rings by looking **across** deals: an entity
graph of deals and the parties, documents and accounts they share.

- **Engine:** deterministic Python behind FastAPI. It produces every scored field; the output is byte-identical run to run and with Nemotron on or off.
- **Agents:** NVIDIA Nemotron. A fraud investigator runs a multi-step tool loop (up to 8 steps, 5 read-only tools over the deal graph, the deals' documents and the loan history) and writes a cited, advisory case for each held ring. A vision model reads documents the parser cannot. Neither can change a scored field.
- **UI:** React + Vite + Tailwind with an interactive fraud graph.

## Quick start

Needs Python 3.11+ and Node 20+. Run every command from the repository root.

**Windows:** plain `python` can open the Microsoft Store stub. Use the launcher (`py -3.13 -m venv .venv`)
or the full path to `python.exe`. In Git Bash, activate with `source .venv/Scripts/activate`.

```bash
# 1. Engine
python -m venv .venv                 # Windows: py -3.13 -m venv .venv
source .venv/bin/activate            # Windows Git Bash: source .venv/Scripts/activate; cmd/PowerShell: .venv\Scripts\activate
pip install -e ".[dev]"

# Needed in EVERY new shell (the documents contain non-ASCII characters):
export PYTHONUTF8=1                  # Windows PowerShell: $env:PYTHONUTF8=1

# 2. Put the challenge data at data/participant/ (it is not in this repo, and data/ is gitignored):
#      data/participant/shared_folder/      one folder per deal
#      data/participant/sim/arrivals.jsonl  when each file arrives
#      data/participant/{history,contacts,rules}/
#    Then:
python -m prism run --folder data/participant/shared_folder --arrivals data/participant/sim/arrivals.jsonl \
    --data-root data/participant --out out/submission.json
python -m prism validate out/submission.json --folder data/participant/shared_folder
#    A new drop or the hidden set runs the same way: point --folder/--arrivals at it, and check that its
#    sim/assignments.json lists every deal folder (a folder missing there gets no credit officer).

# 3. Tests (tests that need data/participant/ skip cleanly without it)
python -m pytest -q
```

### The app

```bash
# API (demo data; MOCK=1 is the default)
uvicorn prism.api.app:app --port 8000

# UI, in a second terminal, from the repository root
cd ui && npm ci && npm run dev       # http://localhost:5173

# UI build and tests
npm --prefix ui run build
npm --prefix ui test
```

The UI proxies `/api` to `http://localhost:8000`. If that port is taken, start the API on another port and
point the UI at it: `PRISM_API=http://localhost:8020 npm --prefix ui run dev`.

The pipeline, deal, inbox, alert and fraud-graph screens run on the made-up demo data in `fixtures/`.
The **Upload & run** page (`http://localhost:5173/#/upload`) runs the real engine on a zipped data set
(a `shared_folder/` plus an `arrivals.jsonl`) and returns the submission JSON for download.
On the fraud graph, **Watch Nemotron investigate** replays a recorded Nemotron run; a ring with no recorded
run gets **Walk through the evidence**, the same replay built from the engine's own evidence.

### Nemotron (optional)

Copy `.env.example` to `.env` and fill in an NVIDIA API key and model IDs. `NEMOTRON_MODE=replay` uses the
recorded calls, so the demo runs offline. The scored output does not depend on Nemotron.

## How the fraud screen decides

Every signal is surprise measured against the bank's own clean loan history (unsupervised anomaly detection).
A deal is held when it shares a rare party with another deal and other kinds of evidence agree; thresholds are
recomputed from whatever history ships with the data and are never fitted to fraud examples. Settings live in
`config/fraud.yaml`.
