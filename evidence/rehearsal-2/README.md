Final clean-checkout rehearsal, 2026-10-11 06:20 Istanbul, with devtools/rehearse.sh on code commit b9073e3 (builder = C5, f72a3e0).
- Fresh clone, no .venv, inputs outside the repo; `python3` = Homebrew 3.14 without the SDK -> builder created .venv via
  `uv sync --frozen --no-dev` and re-ran itself (bootstrap.txt). Env: FREYA_API_KEY; Claude = runner's Team login.
- brief-a: 106 s wall, status ok; brief-ab: 135 s wall, status ok (timings.txt). 2 model calls each (+ CLI-internal Haiku requests, traced as aux_model).
- Artefacts committed inside the clone (b64b21e), devtools/verify_submission.py: 0 problems (verify.txt): clean code commit in both traces,
  workflow/agent ids and export sha match, one run per trace, only artefact paths changed, secret scan clean.
- Extracted policies vs references: ir_diff.txt.
