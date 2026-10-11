# Official-form rehearsal: `build.py {case_folder} {mock_url}` (d6a2f8d, 2026-10-11 19:38-19:41 Istanbul)
`sh devtools/rehearse.sh <new dir> d6a2f8d case <practice case folder: docs/ + contract/> https://rehearsal-placeholder.example.workers.dev`
-> fresh clone, test gate 170 passed, `final_run.sh --commit <folder> <url>` -> builder found the contract inside the folder, real extraction
(2 Claude Opus calls, success), status ok, every tool URL = the given mock URL, save+publish+read-back+export, verify 0 problems,
0 exact IR differences vs the reference. Includes the Turkish/locale and Workers-mock merges. Placeholder URL: nothing was called.
