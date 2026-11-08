#!/usr/bin/env bash
# The AGENTS.md gate in one command, holdout-safe output (D-02).
#   bash scripts/gate.sh          pytest, run, validate, score; prints the tuning-only summary
#   bash scripts/gate.sh --final  also prints the all-13 TOTAL and history lines (03-04's last gate only)
# run / validate / score output goes to files under out/ (they can contain deal names); only exit
# codes, counts and the tuning summary are printed here.
set -u
export PYTHONUTF8=1
PY="${PY:-/c/Users/ezeki/AppData/Local/Programs/Python/Python313/python.exe}"
SD="${PRISM_SAMPLE_DATA:-data/participant}"
FINAL=0
[ "${1:-}" = "--final" ] && FINAL=1
cd "$(dirname "$0")/.." || exit 1
mkdir -p out

"$PY" -m pytest -q > out/gate_pytest.txt 2>&1
rc=$?
echo "pytest: exit $rc; $(tail -n 1 out/gate_pytest.txt)"
if [ $rc -ne 0 ]; then grep -E '^(FAILED|ERROR) ' out/gate_pytest.txt | sed 's/ - .*//' | head -20; exit $rc; fi

"$PY" -m prism run --folder "$SD/shared_folder" --arrivals "$SD/sim/arrivals.jsonl" --out out/submission.json > out/gate_run.txt 2>&1
rc=$?
echo "run: exit $rc; $(grep -m1 '^run:' out/gate_run.txt)"
[ $rc -ne 0 ] && exit $rc

"$PY" -m prism validate out/submission.json --folder "$SD/shared_folder" > out/gate_validate.txt 2>&1
rc=$?
valid=no; grep -qx 'valid' out/gate_validate.txt && valid=yes
echo "validate: exit $rc; valid=$valid errors=$(grep -c '^ERROR' out/gate_validate.txt) warns=$(grep -c '^WARN' out/gate_validate.txt)"
[ $rc -ne 0 ] && exit $rc

"$PY" -m prism score out/submission.json --gold eval/gold.json > out/gate_score.txt 2>&1
rc=$?
echo "score: exit $rc"
[ $rc -ne 0 ] && exit $rc

if [ $FINAL -eq 1 ]; then
  "$PY" scripts/score_split.py out/submission.json --holdout-summary
  grep -E '^(TOTAL|history_flags)' out/gate_score.txt
else
  "$PY" scripts/score_split.py out/submission.json
fi
