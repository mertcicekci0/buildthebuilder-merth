#!/bin/sh
# Same-version regression suite, one simulation at a time (practice cases only).
#   sh devtools/regress.sh <prefix> <slot-for-brief-A> <slot-for-brief-B>
# Results: results/<prefix>-*/ ; summary: devtools/regress_report.py <prefix>-...
set -u
P=$1; SA=$2; SB=$3
cd "$(dirname "$0")/.." || exit 1
. ./env.sh
R=".venv/bin/python devtools/run_cases_ext.py --deadline-min 25"
$R --slot "$SA" --brief A --reps 3 --styles plain --label "$P-a-plain" > "results/$P-a-plain.log" 2>&1
$R --slot "$SB" --brief B --reps 3 --styles plain --label "$P-b-plain" > "results/$P-b-plain.log" 2>&1
$R --slot "$SB" --brief B --reps 6 --styles asks_back,talkative --label "$P-b-styled" > "results/$P-b-styled.log" 2>&1
$R --slot "$SA" --brief A --reps 3 --styles asks_back,talkative,in_words --label "$P-a-styled" > "results/$P-a-styled.log" 2>&1
$R --slot "$SA" --cases cases/calendar.json --only CAL-FRI,CAL-HOL,CAL-XMAS --reps 3 --styles plain --label "$P-a-cal" > "results/$P-a-cal.log" 2>&1
$R --slot "$SB" --cases cases/calendar.json --only CAL-FRI-B --reps 3 --styles plain,pieces,corrects --label "$P-b-cal" > "results/$P-b-cal.log" 2>&1
.venv/bin/python devtools/regress_report.py "$P-a-plain" "$P-b-plain" "$P-b-styled" "$P-a-styled" "$P-a-cal" "$P-b-cal" > "results/$P-report.txt" 2>&1
echo REGRESS_DONE >> "results/$P-report.txt"
