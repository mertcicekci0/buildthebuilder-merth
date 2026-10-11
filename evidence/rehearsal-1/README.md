Preliminary clean-checkout rehearsal, 2026-10-11 04:07 Istanbul.
- `git clone -b dev/overnight` at a571793 into an empty directory; no .venv; inputs outside the repo (A+B brief, kit OpenAPI).
- Env: FREYA_API_KEY (+ URL); Claude access = the runner's Claude Code login (Team). No ANTHROPIC_API_KEY.
- Command: `python3 builder/build.py ../inputs/brief-ab.md ../inputs/brief-ab.openapi.json` with python3 = Homebrew 3.14 (no SDK):
  the builder created .venv via `uv sync --frozen --no-dev` (CPython 3.12.6, claude-agent-sdk 0.2.165) and re-ran itself.
- Result: exit 0, wall 135 s (builder 132 s): 2 model calls, policy checks pass, lint pass, local self-tests pass, dry run valid,
  saved (workflow bbd8a499…, agent be189bd4…, 26 steps/26 tools), read-back pass, published newest pass, exported.
- Extracted policy vs hand-written reference: 0 exact differences. Secret scan of submission/ and traces/: clean.
