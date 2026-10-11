"""Turn a recorded builder session (Claude Code session log kept by the builder) into a replay record for builder/llm.py.

    python devtools/replay_from_session.py <session.jsonl> <case_folder> <mock_url> <replay_dir>

The record is accepted only if the prompt the builder would send now is byte-identical to the prompt recorded in the session
(same case documents, same Freya docs, same guide), so a replayed build reuses exactly that model output."""
import hashlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from builder import generic, inputs as bi, llm  # noqa: E402

PURPOSE = "write the phone-agent workflow from the case documents"


def main():
    sess, case, mock_url, out_dir = sys.argv[1:5]
    rows = [json.loads(x) for x in open(sess, encoding="utf-8") if x.strip()]
    user = next(r for r in rows if r.get("type") == "user" and isinstance((r.get("message") or {}).get("content"), str))
    recorded_prompt = user["message"]["content"]
    output = None
    for r in rows:
        for c in ((r.get("message") or {}).get("content") or []) if isinstance((r.get("message") or {}).get("content"), list) else []:
            if c.get("type") == "tool_use" and c.get("name") == "StructuredOutput":
                output = c.get("input")
    if not output:
        raise SystemExit("no structured output in the session")
    docs, _ = bi.read_brief(case, ROOT / "out" / "inputs" / "replay-check", max_file_chars=40000)
    kit = ROOT / "kit"
    ref = "\n\n".join(f"=== FREYA DOC: {p} ===\n" + (kit / p).read_text() for p in ("docs/workflow-format.md", "docs/tools-and-code.md"))
    example = (kit / "examples/base-agent.json").read_text()
    prompt = (f"MOCK_BASE_URL: {mock_url.rstrip('/')}\n\n{ref}\n\n=== A VALID MINIMAL EXAMPLE WORKFLOW (format only) ===\n{example}\n\n"
              f"=== THE CASE DOCUMENTS ===\n{docs}\n\nWrite the complete workflow for this case.")
    if prompt != recorded_prompt:
        raise SystemExit(f"prompt differs from the recorded session (now {hashlib.sha256(prompt.encode()).hexdigest()[:16]}, "
                         f"recorded {hashlib.sha256(recorded_prompt.encode()).hexdigest()[:16]}): refusing to replay")
    key = llm._key(PURPOSE, generic.GUIDE, prompt, generic.SCHEMA)
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{key}.json").write_text(json.dumps({"purpose": PURPOSE, "model": "claude-opus-5-5", "output": output,
                                                  "source_session": pathlib.Path(sess).name}, ensure_ascii=False))
    print(f"replay record {key}.json from session {pathlib.Path(sess).name}; prompt sha {hashlib.sha256(prompt.encode()).hexdigest()[:16]} identical")


if __name__ == "__main__":
    main()
