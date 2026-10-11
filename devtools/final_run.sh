#!/bin/sh
# Final run of the official procedure (docs/SUBMISSION.md) for one or more briefs, from the repository root.
#   sh devtools/final_run.sh [--commit] <brief1.md> <api1.json> [<brief2.md> <api2.json> ...]
# 1. Refuses to start unless execution-relevant files (builder/, kit/, pyproject.toml, uv.lock, requirements*, .python-version) are
#    committed and clean and the offline tests pass; records that code commit C.
# 2. Per brief N (= brief file name without extension): an earlier attempt's artefacts are moved to archive/final-runs/ (kept, never
#    deleted), then runs exactly `python3 builder/build.py <brief> <api> 2>&1 | tee traces/N.log` and checks exit code, status=ok,
#    the export submission/N.workflow.json and traces/N.jsonl.
# 3. Secret scan of submission/ and traces/.
# 4. With --commit: commits ONLY the artefact files (commit S = C + artefacts) and runs devtools/verify_submission.py on S.
# Never pushes, never edits the builder, never contacts anyone. Needs FREYA_API_KEY and the runner's Claude Code login.
set -u
COMMIT=0
if [ "${1:-}" = "--commit" ]; then COMMIT=1; shift; fi
[ $# -ge 2 ] && [ $(( $# % 2 )) -eq 0 ] || { echo "usage: sh devtools/final_run.sh [--commit] <brief.md> <api.json> [...]"; exit 2; }
cd "$(dirname "$0")/.." || exit 1
ROOT=$(pwd -P)
PY=python3
[ -x .venv/bin/python ] && PY=.venv/bin/python

DIRTY=$(git status --porcelain -- builder kit pyproject.toml uv.lock requirements.hashes.txt requirements.lock.txt .python-version)
if [ -n "$DIRTY" ]; then echo "ABORT: execution-relevant files differ from the last commit:"; echo "$DIRTY"; exit 1; fi
C=$(git rev-parse HEAD) || exit 1
if [ -x .venv/bin/python ]; then
  .venv/bin/python -m pytest -q tests > /dev/null 2>&1 || { echo "ABORT: offline tests fail"; exit 1; }
fi
echo "[final] code commit C=$C ($(git rev-parse --abbrev-ref HEAD)) at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
mkdir -p traces submission archive/final-runs
NAMES=""
FAIL=0
while [ $# -ge 2 ]; do
  BRIEF=$1; API=$2; shift 2
  N=$(basename "$BRIEF"); N=${N%.*}
  NAMES="$NAMES $N"
  if [ -e "traces/$N.log" ] || [ -e "traces/$N.jsonl" ] || [ -e "submission/$N.workflow.json" ]; then
    A="archive/final-runs/$N-$(date -u +%Y%m%dT%H%M%SZ)"
    mkdir -p "$A" && for f in "traces/$N.log" "traces/$N.jsonl" "submission/$N.workflow.json" "out/$N.policy.json"; do [ -e "$f" ] && cp -p "$f" "$A/"; done
    rm -f "traces/$N.log" "traces/$N.jsonl" "submission/$N.workflow.json"
    echo "[final] earlier attempt of $N kept in $A"
  fi
  S=$(date +%s)
  ( python3 builder/build.py "$BRIEF" "$API" 2>&1; echo "__exit=$?" > "archive/final-runs/.$N.exit" ) | tee "traces/$N.log"
  RC=$(sed -n 's/^__exit=//p' "archive/final-runs/.$N.exit"); rm -f "archive/final-runs/.$N.exit"
  echo "[final] $N: exit $RC, wall $(( $(date +%s) - S )) s"
  if [ "$RC" != "0" ] || ! grep -q "status=ok" "traces/$N.log" || [ ! -s "submission/$N.workflow.json" ] || [ ! -s "traces/$N.jsonl" ]; then
    echo "[final] $N: NOT OK (exit/status/export/trace) - inspect traces/$N.log before submitting"; FAIL=1
  fi
  grep -q "commit=$C" "traces/$N.log" || { echo "[final] $N: trace does not name the clean code commit $C"; FAIL=1; }
done
$PY devtools/secret_scan.py submission traces || { echo "[final] secret scan found something: do not commit"; FAIL=1; }
if [ "$FAIL" != "0" ]; then echo "[final] problems found; nothing committed"; exit 1; fi
FILES=""
for n in $NAMES; do FILES="$FILES submission/$n.workflow.json traces/$n.log traces/$n.jsonl"; done
if [ "$COMMIT" = "1" ]; then
  git add $FILES && git commit -q -m "Final-run artefacts for$NAMES (code commit $C)" || exit 1
  echo "[final] artefact commit S=$(git rev-parse HEAD)"
  $PY devtools/verify_submission.py $NAMES || exit 1
else
  echo "[final] OK. To commit only the artefacts:"
  echo "  git add$FILES && git commit -m \"Final-run artefacts for$NAMES (code commit $C)\""
  echo "  $PY devtools/verify_submission.py$NAMES"
fi
