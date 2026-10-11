"""Render docs/RESOURCE_AUDIT.md from the audit workflow result (audit.json) plus the integration decisions."""
import json, re, sys

o = json.load(open(sys.argv[1]))
out = sys.argv[2]

INV = {}
for line in open(sys.argv[3]):
    p, h1, h2, n = line.split()
    INV[p[2:]] = (h1, h2, int(n))

SHA_FULL = {}  # recomputed full sha256 (filled by caller via argv[4] json)
SHA_FULL.update(json.load(open(sys.argv[4])))

# Final decisions after integration (overrides / status for the readers' recommendations), matched by capability prefix.
DECISIONS = [
    (r"^Guard and trace the phone-agent model", "ADOPTED IN PART (C14 8cb1932)",
     "Traced in every build (trace step 'phone-agent model', with an `overridden` flag). Rejecting an override was NOT adopted: "
     "kit/freya.py:83 says organisers may point a test at another model with these two variables, so failing would break their test."),
    (r"^Check before the final for a kit update", "ADOPTED (checklist)",
     "docs/SUBMISSION.md pre-final checklist: check the official page/email for a kit version > 3 and re-run `(cd kit && shasum -a 256 -c ../kit.sha256)`."),
    (r"^Lint for multiple transitions with the same target_node", "ADOPTED (C14 8cb1932)",
     "builder/validate.py lint + tests/test_c14_review.py::test_lint_flags_transitions_the_engine_would_merge_and_previous_outside_globals."),
    (r"^Lint: __previous__ allowed only in global nodes", "ADOPTED (C14 8cb1932)", "Same lint/test as above."),
    (r"^Fail fast when FREYA_API_KEY is missing", "ADOPTED (C14 8cb1932)",
     "build.py stops before any model call; tests/test_c14_review.py::test_build_stops_before_model_calls_without_a_freya_key."),
    (r"^Use the skill's checklist as an independent validation crosswalk", "ADOPTED (this document, section 'Skill crosswalk')", ""),
    (r"^Record agentVersionNumber per run", "DEFERRED",
     "Devtools-only; the c5 chain (registry agent = workflow.agentId = export agent_id = simulation agent, published version) was verified "
     "on 2026-10-11 (docs/DAY_PLAN.md:13-14). Not worth the time before the 19:30 stop."),
    (r"^Rebuild the selected commit several times", "PARTIAL",
     "Live extractions of the same builder: 29 overnight + R5/R6 (C13) + the clean-checkout rehearsal of the promoted commit. A dedicated "
     "N-rebuild variance study was not run (Claude allowance hit its session limit 12:20-13:40 and again until 17:00)."),
]


def decision(u):
    for pat, status, note in DECISIONS:
        if re.search(pat, u["capability"]):
            return status, note
    rec = u["recommendation"]
    if rec in ("test", "adopt"):
        return "DEFERRED (not run before the 19:30 stop)", "Recommended '" + rec + "' by the reader; not done today: limited time, " \
            "one Freya simulation slot, and the Claude session limit. Listed as a next action."
    return rec.upper(), ""


def clean_path(p):
    p = p.split("freyahackathon/")[-1]
    p = re.split(r";|\)| identical| \(", p)[0].strip()
    return p


def md(s):
    return str(s).replace("|", "\\|").replace("\n", " ")


L = []
A = L.append
A("# Starter-kit resource audit (kit VERSION 3) — 2026-10-11")
A("")
A("Scope: every file supplied in the starter kit (`/Users/mertcicekci/Downloads/build-the-builder 2/` and the repository copy `kit/`, "
  "byte-identical: `diff -rq --exclude=__pycache__` empty, re-checked 16:4x Istanbul), hidden files included, plus the benchmark paper the "
  "organisers' email references (arXiv:2609.04611, local copy `archive/docs-snapshots/arxiv-2609.04611.pdf`). Official rules (quoted in "
  "docs/SUBMISSION.md) govern wherever the kit README, the skill, the example builder or the paper differ.")
A("")
A("**What this audit is and is not.** Every file was read completely by an independent reader agent (lines_read == lines_total for all 17 "
  "entries; the PDF in pages, 41/41). Reading a file is not using it, and using it is not evidence of benefit: each entry separates "
  "*implemented at* (code reference) from *evidence* (a measured run or test) and lists unverified gaps explicitly. This document does not "
  "claim that the kit is fully utilised or that the builder is fully compliant.")
A("")
A("Method: workflow `kit-resource-audit` (run wf_21c0c28f-cc0): 9 reader agents (README/VERSION/env, api.md, workflow-format.md, "
  "tools-and-code.md, briefs + public test set, tool OpenAPI + base agent, freya.py + run_cases.py + score.py, skill + example builder, paper), "
  "each told to read whole files and cite lines. The planned completeness critic **did not run** (Claude session limit, 'resets 5pm'); the "
  "coverage cross-check below was done by the integration owner instead. Line references to our code point at candidate C13 (fh-c10, "
  "a51f13a) unless stated; C14 (947438f, 8cb1932) changed only the lines named in the decisions.")
A("")
A("## Inventory and coverage cross-check")
A("")
A("| # | File (kit-relative) | sha256 (full, recomputed) | prefix in both copies (inventory-hashes.txt) | lines (wc -l) | read by auditor |")
A("|---|---|---|---|---|---|")
by_path = {}
for e in o["entries"]:
    by_path[clean_path(e["path"]).replace("kit/", "", 1)] = e
for i, (p, (h1, h2, n)) in enumerate(sorted(INV.items()), 1):
    e = by_path.get(p)
    rd = f"{e['lines_read']}/{e['lines_total']}" if e else "MISSING"
    full = SHA_FULL.get(p, "?")
    ok = "same" if h1 == h2 and full.startswith(h1) else "MISMATCH"
    A(f"| {i} | `{p}` | `{full}` | {h1} ({ok}) | {n} | {rd} |")
A(f"| 17 | paper `arxiv-2609.04611.pdf` (not a kit file) | `{SHA_FULL.get('paper', '?')}` | — | 41 pages | 41/41 pages |")
A("")
A("Cross-check result: 16 of 16 supplied files have an entry, each read in full; the two `+1` line counts (tools/openapi.json 472 vs 471, "
  "examples/base-agent.json 407 vs 406) are files without a final newline (last byte `}`), not unread lines. Not supplied files: "
  "`kit/__pycache__/`, `kit/tools/__pycache__/` (generated by our imports; excluded). `.env`-style secrets: none in the kit (env.example holds "
  "placeholders only).")
A("")
A("## Actions taken from this audit (and from the independent C10–C13 review run alongside it)")
A("")
A("| Change | Commit (dev/c10) | Validation |")
A("|---|---|---|")
A("| Trace phone-agent model; stop before model calls without FREYA_API_KEY; lint same-target transitions and `__previous__` outside globals | 8cb1932 | 106 offline tests; kit golden workflow unchanged |")
A("| Review fixes: partial-plan guard key order-independent; bare-tens date guard blocks once per utterance; outcome/reason/product-id/product-type role inference on renamed APIs; extraction guide defines max_asks, refusals_before_close, alternative_asks, after_now | 947438f | inferred roles unchanged on kit + R1/R2/R3/R5/R6 APIs; kit golden diff = two hook edits only; 7 of 10 new tests fail on C13, pass on C14 |")
A("| Full practice regression of C14 (99 runs, same suite as r5/r9) | — | see docs/DAY_PLAN.md log (r14) |")
A("")
A("Rejected recommendation: failing the build when the agent-model variables are set (see README entry). The paper's 'interview the "
  "client' finding is not applicable: the rules allow no manual help or client contact during the 30-minute build.")
A("")
A("## Skill crosswalk (kit/.claude/skills/build-phone-agent/SKILL.md vs our builder)")
A("")
sk = by_path.get(".claude/skills/build-phone-agent/SKILL.md")
if sk:
    A("| Skill line | Rule | Where our builder does it | Evidence |")
    A("|---|---|---|---|")
    for f in sk["findings"]:
        A(f"| {md(f['ref'])} | {md(f['finding'])} | {md(f.get('implemented_at', ''))} | {md(f.get('evidence', ''))} |")
    A("")
A("The skill and kit/examples/agent_builder.py were **read, never invoked**. No benefit or harm is demonstrated; the decision not to use them "
  "(docs/DAY_PLAN.md:51-57) rests on reasoning: the hidden briefs have no practice customers, so the skill's test-and-fix loop, its main "
  "lever, cannot run in the final, and the organisers' 10/10 claim (README L5) is a single run tuned on the same customers.")
A("")
A("## Paper vs official rules (rules win)")
A("")
A("| Paper finding | Our position | Rule that governs |")
A("|---|---|---|")
A("| Policies are searched, not read (sec 6) | The extractor receives the whole brief; an audit call re-reads it against the IR; evidence quotes are checked verbatim | Brief + tool API are the only inputs (SUBMISSION.md) |")
A("| Interviewing the client triples scores (sec 6, App. I) | Not possible: no human help during the build | 'no manual steps' during the 30-minute build |")
A("| Deterministic guards beat a single LLM loop (sec 5, Fig 5) | Core design: dates, amounts, attempts, recording are code/oracle tools; prompts carry wording only | — (design choice, measured: C5/C9 93-94/99 vs base agent 0/13) |")
A("| Self-written tests overfit (sec 6, App. C) | Practice runs treated as probes; never weakened tests; independent variant briefs R1–R6; hidden-brief generality remains unverified | Hidden briefs are scored on unseen customers, 3 runs each |")
A("| Quiet API defects, ambiguous writes (App. H) | create_promise retry is bounded; an 'already has a promise' answer after a timeout is unverified (gap) | Tool API is the organisers' mock |")
A("| One build trial per task; variance uncharacterised (sec 7) | Top-10 builders are re-run by organisers: low variance matters; rebuild variance only partly measured (see decisions) | Re-run rule (SUBMISSION.md) |")
A("| Attempts to reach held-out data are common (sec 5) | We do not process hidden briefs before release and never hard-code case answers | Rules: no hidden-brief access, no probing/load tests |")
A("")
A("## Per-file entries")
for e in o["entries"]:
    p = clean_path(e["path"])
    key = p.replace("kit/", "", 1)
    A("")
    A(f"### `{p}`")
    A("")
    inv = INV.get(key)
    if inv:
        A(f"sha256 `{SHA_FULL.get(key, '?')}` · {inv[2]} lines · read {e['lines_read']}/{e['lines_total']}")
    else:
        A(f"sha256 `{SHA_FULL.get('paper', '?')}` · read {e['lines_read']}/{e['lines_total']} pages")
    A("")
    A("**Purpose.** " + md(e["purpose"]))
    A("")
    A("| Ref | Finding | Implemented at | Evidence / gap |")
    A("|---|---|---|---|")
    for f in e["findings"]:
        A(f"| {md(f.get('ref', ''))} | {md(f['finding'])} | {md(f.get('implemented_at', ''))} | {md(f.get('evidence', ''))} |")
    if e.get("unadopted"):
        A("")
        A("Unadopted capabilities:")
        A("")
        A("| Capability | Ref | Reader recommends | Final decision | Why |")
        A("|---|---|---|---|---|")
        for u in e["unadopted"]:
            st, note = decision(u)
            A(f"| {md(u['capability'])} | {md(u.get('ref', ''))} | {u['recommendation']} | **{st}** | {md(note or u.get('reason', ''))} |")
    if e.get("gaps"):
        A("")
        A("Unverified gaps:")
        A("")
        for g in e["gaps"]:
            A("- " + md(g))
open(out, "w").write("\n".join(L) + "\n")
print(len(L), "lines")
