#!/bin/sh
# Clean-checkout rehearsal of the official procedure (docs/SUBMISSION.md), fully automatic. Uses the clone's devtools/final_run.sh when present.
#   sh devtools/rehearse.sh <workdir> <commit> <name1> <brief1> <api1> [<name2> <brief2> <api2>]
# Clones this repository at <commit> into <workdir>/repo (no .venv, no local state), copies the inputs to <workdir>/inputs/<name>.md
# and <name>.openapi.json, runs `python3 builder/build.py ... | tee traces/<name>.log` for each brief with wall-clock timing,
# commits the artefacts inside the clone (never in this repository) and runs devtools/verify_submission.py on that commit.
# Needs FREYA_API_KEY in the environment and the runner's Claude Code login. Creates/updates one Freya workflow per brief name.
set -u
W=$1; C=$2; shift 2
SRC="$(cd "$(dirname "$0")/.." && pwd)"
rm -rf "$W" && mkdir -p "$W/inputs" || exit 1
git clone -q "$SRC" "$W/repo" && (cd "$W/repo" && git checkout -q "$C") || exit 1
NAMES=""
while [ $# -ge 3 ]; do
  cp "$2" "$W/inputs/$1.md" && cp "$3" "$W/inputs/$1.openapi.json" || exit 1
  NAMES="$NAMES $1"; shift 3
done
cd "$W/repo" || exit 1
mkdir -p traces submission
if [ -f devtools/final_run.sh ]; then   # rehearse the exact final procedure (commit inside the throwaway clone only)
  git config user.name rehearsal && git config user.email rehearsal@localhost
  ARGS=""
  for n in $NAMES; do ARGS="$ARGS ../inputs/$n.md ../inputs/$n.openapi.json"; done
  sh devtools/final_run.sh --commit $ARGS 2>&1 | tee ../final_run.txt | grep "^\[final\]\|problem(s)" | tee ../timings.txt
  grep "problem(s)" ../final_run.txt > ../verify.txt
  exit 0
fi
for n in $NAMES; do
  s=$(date +%s)
  python3 builder/build.py "../inputs/$n.md" "../inputs/$n.openapi.json" 2>&1 | tee "traces/$n.log" > /dev/null
  echo "$n wall_s=$(( $(date +%s) - s ))" | tee -a ../timings.txt
done
for n in $NAMES; do git add "submission/$n.workflow.json" "traces/$n.log" "traces/$n.jsonl"; done
git -c user.name=rehearsal -c user.email=rehearsal@localhost commit -q -m "rehearsal artefacts (local clone only)" || exit 1
echo "artefact commit: $(git rev-parse --short HEAD) on code commit $(git rev-parse --short "$C")" | tee -a ../timings.txt
.venv/bin/python devtools/verify_submission.py $NAMES 2>&1 | tee ../verify.txt
