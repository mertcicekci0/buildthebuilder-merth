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
[ -n "$W" ] && [ ! -e "$W" ] || { echo "ABORT: work directory must be a new path (never reused or deleted): '$W'"; exit 1; }
mkdir -p "$W/inputs" || exit 1
git clone -q "$SRC" "$W/repo" && (cd "$W/repo" && git checkout -q "$C") || exit 1
NAMES=""
ARGS=""
while [ $# -ge 3 ]; do   # inputs keep their extension: a .md brief, or a .zip / folder bundle (the contract may be the same bundle)
  bb=$(basename "$2"); ba=$(basename "$3")
  case "$bb" in *.*) EB=".${bb##*.}";; *) EB="";; esac
  case "$ba" in *.*) EA=".${ba##*.}";; *) EA="";; esac
  case "$3" in https://*|http://*) API="$3";; *) cp -R "$3" "$W/inputs/$1.contract$EA" || exit 1; API="../inputs/$1.contract$EA";; esac
  cp -R "$2" "$W/inputs/$1$EB" || exit 1
  NAMES="$NAMES $1"; ARGS="$ARGS ../inputs/$1$EB $API"; eval "IN_$(echo "$1" | tr -c 'A-Za-z0-9_\n' _)=\"../inputs/$1$EB ../inputs/$1.contract$EA\""; shift 3
done
cd "$W/repo" || exit 1
mkdir -p traces submission
if [ -f devtools/final_run.sh ]; then   # rehearse the exact final procedure (commit inside the throwaway clone only)
  git config user.name rehearsal && git config user.email rehearsal@localhost
  sh devtools/final_run.sh --commit $ARGS 2>&1 | tee ../final_run.txt | grep "^\[final\]\|problem(s)" | tee ../timings.txt
  grep "problem(s)" ../final_run.txt > ../verify.txt
  exit 0
fi
for n in $NAMES; do
  s=$(date +%s)
  eval "set -- \$IN_$(echo "$n" | tr -c 'A-Za-z0-9_\n' _)"   # this name's recorded input paths (fallback for commits without final_run.sh)
  python3 builder/build.py "$1" "$2" 2>&1 | tee "traces/$n.log" > /dev/null
  echo "$n wall_s=$(( $(date +%s) - s ))" | tee -a ../timings.txt
done
for n in $NAMES; do git add "submission/$n.workflow.json" "traces/$n.log" "traces/$n.jsonl"; done
git -c user.name=rehearsal -c user.email=rehearsal@localhost commit -q -m "rehearsal artefacts (local clone only)" || exit 1
echo "artefact commit: $(git rev-parse --short HEAD) on code commit $(git rev-parse --short "$C")" | tee -a ../timings.txt
.venv/bin/python devtools/verify_submission.py $NAMES 2>&1 | tee ../verify.txt
