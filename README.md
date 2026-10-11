# Freya "Build the Builder" — team Mert

Builder: reads one bank brief (Markdown) and its tool API (OpenAPI JSON) and creates a Freya phone agent.
Contract (organisers): `python3 builder/build.py {brief_path} {tool_api_path}`.
Status (2026-10-11): fully automatic. The model reads the brief into a typed policy (2 Claude calls via the Agent SDK), code checks and compiles it, saves and exports the agent. On the public briefs and our raw-material variant the extracted policy matches the hand-written references on every structured field (`evidence/`); live practice results are in `docs/MORNING_REPORT.md`.

## Setup (macOS/Linux, Python >= 3.10)

`python3 builder/build.py ...` works with any `python3`: if that interpreter lacks `claude-agent-sdk`, the builder uses the
repo's `.venv`, creating it first from the lock files (`uv sync --frozen --no-dev`, or `pip --require-hashes` when uv is
missing) and re-runs itself there. It prints each step. To prepare it yourself:

```bash
uv sync                       # creates .venv from uv.lock (exact versions + hashes)
# or: python3 -m venv .venv && .venv/bin/pip install -r requirements.hashes.txt --require-hashes
cp env.example env.sh && chmod 600 env.sh   # fill in keys; env.sh is gitignored
set -a && . ./env.sh && set +a
.venv/bin/python devtools/check_env.py      # presence/permissions + one read-only Freya call
```

Pinned: claude-agent-sdk 0.2.165 (bundles Claude Code CLI 2.1.294), jsonschema 4.26.0, pytest 9.1.1, CPython 3.12.6.

### Credentials
| Variable | Purpose |
|---|---|
| `FREYA_API_KEY` | Freya workspace key (`fak_...`). One key per team. |
| `FREYA_BANK_TEAM_KEY` | Stable practice-bank team key (16+ chars). Never change it. |
| Claude Code authentication | Builder model access goes through the Claude Agent SDK, whose bundled Claude Code CLI uses whatever credential the runner has: a claude.ai Pro/Max/Team/Enterprise login (`claude auth login`), `CLAUDE_CODE_OAUTH_TOKEN` (`claude setup-token`), or optionally `ANTHROPIC_API_KEY`. No separate API key is required. Check with `claude auth status`. |
| `BUILDER_MODEL` | Default `claude-opus-5-5`. |

Secrets: `env.sh` (600, gitignored); every log/trace passes through `devtools/redact.py`; `.git/hooks/pre-commit` runs `devtools/secret_scan.py --staged`; run `devtools/secret_scan.py submission/` before handing in.

## Layout
| Path | |
|---|---|
| `kit/` | Pristine kit v3, read-only, sha256-verified against the official zip (`kit.sha256`). |
| `builder/` | The submitted builder: `build.py` (orchestrator, 27-min internal deadline), `openapi_tools.py` (tool contracts + field roles), `policy_schema.py` (IR schema + checks), `extract.py` + `llm.py` (the only model use), `compile_workflow.py` (template), `runtime_src.py` (deterministic logic embedded in the agent), `validate.py`, `local_exec.py`, `trace.py`. |
| `devtools/` | Development tools (not part of the builder). |
| `probes/` | Platform-semantics experiments and `probes/README.md` (verified findings). |
| `cases/` | Extra case files: custom scripts/expectations over the hosted fixtures PUB-01..13. |
| `out/ results/ exports/ traces/` | Run outputs (gitignored); final artefacts go to `submission/` (tracked). |

## Builder
```bash
python3 builder/build.py brief.md tools.openapi.json                       # full run (model reads the brief)
python3 builder/build.py brief.md tools.openapi.json --ir policy.json      # compile a given policy, no model call
python3 builder/build.py brief.md tools.openapi.json --no-save             # stop before writing to Freya
.venv/bin/python -m pytest -q                                              # offline tests, no network
```

## Development tools
```bash
.venv/bin/python devtools/run_cases_ext.py --agent <agent_id> --brief A                 # like kit run_cases
.venv/bin/python devtools/run_cases_ext.py --slot <slot> --brief B --reps 3 --styles pieces,corrects,in_words
.venv/bin/python devtools/run_cases_ext.py --agent <id> --now 2026-10-16T15:00           # another calendar day
.venv/bin/python devtools/run_cases_ext.py --collect --label <label>                     # resume scoring a run
.venv/bin/python devtools/collect_sim.py <simulation_id>                                 # score any finished simulation
.venv/bin/python devtools/chat.py <workflow_id> "Hello" "Yes, speaking." --case PUB-01 --now 2026-10-16T15:00
.venv/bin/python devtools/fetch_call.py <call_id> --vars                                 # readable call timeline
.venv/bin/python devtools/bank.py ledger <customer_id>
```
`run_cases_ext.py` packs all repetitions into one simulation (own bank session per repetition), has a hard deadline and saves transcripts of failures under `results/<label>/calls/`.

## Clean-run procedure (what organisers do; rehearsed, see docs/MORNING_REPORT.md)
```bash
git clone <repo> && cd <repo> && git checkout <commit>
export FREYA_API_KEY=...        # their workspace key (FREYA_API_URL optional)
claude auth status              # Claude access: the runner's own Claude Code login, CLAUDE_CODE_OAUTH_TOKEN, or ANTHROPIC_API_KEY
python3 builder/build.py briefs/hidden-1.md briefs/hidden-1.openapi.json 2>&1 | tee traces/hidden-1.log
```
Outputs: `submission/<brief>.workflow.json` (export of the saved agent), `traces/<brief>.jsonl` (machine-readable trace),
`out/<brief>.policy.json` (extracted policy). Typical run: 2-3 minutes, 2 model calls (internal hard stop at 27 minutes).
