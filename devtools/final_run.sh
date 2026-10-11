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
# A rerun of only one brief makes the briefs' traces name different commits (C and S1): rerun ALL briefs together from one commit.
# Never pushes, never edits the builder, never contacts anyone. Needs FREYA_API_KEY and the runner's Claude Code login.
set -u
COMMIT=0
if [ "${1:-}" = "--commit" ]; then COMMIT=1; shift; fi
[ $# -ge 2 ] && [ $(( $# % 2 )) -eq 0 ] || { echo "usage: sh devtools/final_run.sh [--commit] <brief.md> <api.json> [...]"; exit 2; }
cd "$(dirname "$0")/.." || exit 1
ROOT=$(pwd -P)
PY=python3
[ -x .venv/bin/python ] && PY=.venv/bin/python

# Pre-flight (nothing is built if any check fails):
# - every brief/api file exists; brief file names (without extension) are distinct, because N names the Freya slot, the trace and the export
# - nothing is staged in the index (the artefact commit must contain only the artefact files)
# - the phone-agent model is not overridden in this shell: kit/freya.py:83-84 says "Do not change it"; the two variables are for the
#   organisers' own tests, not for our submission build
STEMS=""
i=1
for f in "$@"; do
  [ -e "$f" ] || { echo "ABORT: input not found: $f"; exit 2; }   # a file, or a folder/zip bundle (builder/inputs.py)
  case "$f" in *" "*) echo "ABORT: input path contains a space: '$f'; copy it to e.g. briefs/final.zip"; exit 2;; esac
  if [ $(( i % 2 )) -eq 1 ]; then b=$(basename "$f"); b=${b%.*}
    case " $STEMS " in *" $b "*) echo "ABORT: two briefs share the name '$b'; copy them to distinct names (e.g. briefs/hidden-1.md, briefs/hidden-2.md)"; exit 2;; esac
    STEMS="$STEMS $b"
  fi
  i=$((i + 1))
done
git diff --cached --quiet || { echo "ABORT: files are staged in the index; unstage them (git restore --staged <paths>) so the artefact commit stays clean"; exit 1; }
[ -z "${BUILDER_BASE_URL_OVERRIDE:-}" ] || { echo "ABORT: BUILDER_BASE_URL_OVERRIDE is set (test builds against our mock only); unset it"; exit 1; }
if [ -n "${FREYA_AGENT_PROVIDER:-}${FREYA_AGENT_MODEL:-}" ]; then
  echo "ABORT: FREYA_AGENT_PROVIDER/FREYA_AGENT_MODEL are set in this shell; unset them (the submission must use the event's model)"; exit 1
fi
DIRTY=$(git status --porcelain -- builder kit pyproject.toml uv.lock requirements.hashes.txt requirements.lock.txt .python-version)
if [ -n "$DIRTY" ]; then echo "ABORT: execution-relevant files differ from the last commit:"; echo "$DIRTY"; exit 1; fi
C=$(git rev-parse HEAD) || exit 1
# The offline tests are a required gate, never skipped: a fresh checkout has no .venv (or one built by the builder with --no-dev, so
# without pytest); then the locked dev group is installed with `uv sync --frozen` (same pinned versions + pytest 9.1.1).
if ! { [ -x .venv/bin/python ] && .venv/bin/python -c "import pytest" > /dev/null 2>&1; }; then
  command -v uv > /dev/null 2>&1 || { echo "ABORT: no pytest in .venv and no uv to install the locked dev group; cannot run the offline tests"; exit 1; }
  echo "[final] installing the locked environment incl. the dev group (uv sync --frozen) to run the offline tests"
  uv sync --frozen -q || { echo "ABORT: uv sync --frozen failed; cannot run the offline tests"; exit 1; }
fi
TESTS=$(.venv/bin/python -m pytest -q -p no:cacheprovider tests 2>&1) || { echo "$TESTS" | tail -15; echo "ABORT: offline tests fail"; exit 1; }
PY=.venv/bin/python
echo "[final] offline tests: $(echo "$TESTS" | tail -1)"
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
    if git cat-file -e "HEAD:traces/$N.log" 2>/dev/null; then
      echo "[final] $N: the committed artefacts of the earlier run were moved to archive/final-runs; to restore them: git checkout -- traces/$N.log traces/$N.jsonl submission/$N.workflow.json"
    fi
  elif grep -q "status=ok with warnings" "traces/$N.log"; then
    echo "[final] WARNING $N: build ended 'ok with warnings' (a self-test or read-back check failed) - read traces/$N.log and decide before submitting"
  fi
  grep -q "commit=$C" "traces/$N.log" || { echo "[final] $N: trace does not name the clean code commit $C"; FAIL=1; }
done
$PY devtools/secret_scan.py submission traces || { echo "[final] secret scan found something: do not commit"; FAIL=1; }
if [ "$FAIL" != "0" ]; then echo "[final] problems found; nothing committed"; exit 1; fi
FILES=""
for n in $NAMES; do FILES="$FILES submission/$n.workflow.json traces/$n.log traces/$n.jsonl"; [ -s "out/$n.policy.json" ] && FILES="$FILES out/$n.policy.json"; done
if [ "$COMMIT" = "1" ]; then
  git add -f $FILES && git commit -q -m "Final-run artefacts for$NAMES (code commit $C)" -- $FILES || exit 1
  echo "[final] artefact commit S=$(git rev-parse HEAD)"
  $PY devtools/verify_submission.py $NAMES || exit 1
else
  echo "[final] OK. To commit only the artefacts:"
  echo "  git add -f$FILES && git commit -m \"Final-run artefacts for$NAMES (code commit $C)\" --$FILES"
  echo "  $PY devtools/verify_submission.py$NAMES"
fi
