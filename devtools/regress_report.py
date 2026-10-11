"""Summarise saved simulation results, keeping bank-record correctness and conversation checks apart.

    python devtools/regress_report.py r2-a-plain r2-b-plain ...        # labels under results/

Per label: runs; runs whose ledger matched the expectation (outcome, reason, promises, notes, callbacks, transfers);
runs the judge passed on the conversation checks; runs that passed both; cases that passed every repetition.
A run's failure strings come from the kit's scorer: the ones starting with "conversation:" are the judge's, all
others are ledger mismatches.
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def summarise(label):
    s = json.loads((ROOT / "results" / label / "summary.json").read_text())
    rows = s["rows"]
    ledger_ok = [r for r in rows if not any(not f.startswith("conversation") for f in r["failed"])]
    conv_ok = [r for r in rows if not any(f.startswith("conversation") for f in r["failed"])]
    both = [r for r in rows if r["pass"]]
    cases = {}
    for r in rows:
        cases.setdefault(r["key"].split("#")[0], []).append(r["pass"])   # run_id for custom cases
    case_ok = {k: all(v) for k, v in cases.items()}
    return {"label": label, "agent_id": s["agent_id"], "styles": s.get("styles"), "now": s.get("now"), "runs": len(rows),
            "ledger_ok": len(ledger_ok), "conversation_ok": len(conv_ok), "run_pass": len(both),
            "cases_all_reps": sum(case_ok.values()), "cases": len(case_ok),
            "failures": [(r["key"], r.get("style"), f[:220]) for r in rows for f in r["failed"]]}


def main(labels):
    tot = {"runs": 0, "ledger_ok": 0, "conversation_ok": 0, "run_pass": 0, "cases_all_reps": 0, "cases": 0}
    for lb in labels:
        x = summarise(lb)
        for k in tot:
            tot[k] += x[k]
        print(f"{lb:<22} agent {x['agent_id'][:8]} styles={x['styles'] or 'plain'} now={x['now'] or 'case'}: runs {x['runs']}, "
              f"ledger correct {x['ledger_ok']}/{x['runs']}, conversation checks {x['conversation_ok']}/{x['runs']}, "
              f"run passes {x['run_pass']}/{x['runs']}, cases passing every rep {x['cases_all_reps']}/{x['cases']}")
        for key, style, f in x["failures"]:
            print(f"    FAIL {key} [{style or 'plain'}] {f}")
    print(f"{'TOTAL':<22} runs {tot['runs']}, ledger correct {tot['ledger_ok']}, conversation checks {tot['conversation_ok']}, "
          f"run passes {tot['run_pass']}, cases passing every rep {tot['cases_all_reps']}/{tot['cases']}")


if __name__ == "__main__":
    main(sys.argv[1:])
