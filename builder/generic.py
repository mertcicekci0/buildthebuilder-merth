"""General builder path: for a case that the collections template cannot express (no OpenAPI contract with its tools, or another
kind of call), the model reads every case document plus Freya's workflow documentation and writes the workflow itself; the
deterministic checks (lint, URL binding, size) and Freya's dry run validate it, with bounded repair rounds. Nothing about any
particular case is written here: every rule, value, name, path and wording comes from the documents at run time."""
import json
import re

from builder import inputs as bi
from builder import validate as v

GUIDE = """You are the construction step of an automatic agent builder. From a bank's case documents you write ONE complete,
production-quality Freya phone-agent workflow (JSON) that the bank's customers will talk to. Simulated customers test it; reviewers
read it. You never see the customers. Build for correctness, policy compliance, honesty and natural spoken conversation.

How to read the documents:
- Read the brief first, then the readme, then every source. Sources may disagree: decide from the documents themselves which one
  governs (later explicit decisions, recaps and meeting transcripts usually amend earlier analyses; a document that says it
  supersedes another wins). Apply every governing rule; never apply a superseded one. Do not invent rules, values or wording.
- Example conversations show the expected behaviour; generalise from them, do not hard-code their customers.
- Test-customer files are data for the bank's mock, never for the agent: the agent must never contain customer data.
- Required wording (fixed announcements, disclosures, sentences the documents prescribe) must be used exactly, in the documents'
  customer language. The agent always speaks the language the documents say the calls are in.

How to build (follow the Freya format documentation below exactly; it is authoritative for the JSON):
- Call variables: use exactly the names the documents give (as {{name}} in prompts and tool defaults). Every date decision uses the
  call-time variable the documents name, never the system clock; do date arithmetic in code (code nodes / tool hooks), not in prompts.
- Every bank service in the service contract that the agent needs is an api_call tool. Its url default is MOCK_BASE_URL followed by
  the contract's path; method, headers and body/query fields exactly as the contract names them. Fixed values (url, method, headers,
  values from call variables) go in `default`; values the model must fill are normal parameters with precise descriptions. A path
  parameter that comes from a call variable is written as {{variable}} in the url default; one chosen during the conversation is a
  model parameter that a pre_process_code hook puts into config['url'].
- Interpret every documented response field and error status (HTTP codes, error codes, empty lists): decide in code or transitions,
  and tell the model in plain words what to say. Never claim an action succeeded (an update, a sent e-mail, a transfer) unless the
  tool result says it did; if a call fails, say so honestly and follow the documented fallback.
- Read amounts, currencies and dates exactly as the service returns them, spoken naturally in the call language; never round or
  convert unless the documents say so.
- Disclosure: say only what the documents allow, to whom they allow it; mask what they say to mask.
- Hand-offs (to a human agent, or ending the call) follow the documents: use the documented service or mechanism; say the documented
  sentence; end the call cleanly with an end node.
- Keep it compact so it can be written in one pass: about 4 to 8 nodes, rules stated once (in system_prompt or the node that needs
  them), no repeated text, short tool descriptions; hooks only where a value must be computed or checked in code.
- Keep the graph simple and robust: a short opening, one main conversation node that holds the tools it needs (or a few clear
  stages), global rules in system_prompt, explicit end_call nodes. Every node must be reachable and every path must reach an end.
- Use only node types, fields and tool handlers that the Freya documentation describes. Hooks and code nodes follow its rules
  (never raise, use vars, no double braces in code).
Return the workflow object and short design notes (which sources governed which decisions, and any conflicts you resolved)."""

SCHEMA = {"type": "object", "properties": {"workflow": {"type": "object"}, "design_notes": {"type": "string"}},
          "required": ["workflow", "design_notes"]}


def api_urls(wf):
    out = []
    for where, node in list((wf.get("nodes") or {}).items()) + [("global_" + k, n) for k, n in (wf.get("global_nodes") or {}).items()]:
        for t in (node or {}).get("tools") or []:
            if (t.get("function") or {}).get("handler") == "__function__:api_call":
                out.append((where, t["function"].get("name"), ((t["function"].get("parameters") or {}).get("properties") or {}).get("url", {}).get("default")))
    for t in wf.get("tools") or []:
        if (t.get("function") or {}).get("handler") == "__function__:api_call":
            out.append(("tools", t["function"].get("name"), ((t["function"].get("parameters") or {}).get("properties") or {}).get("url", {}).get("default")))
    return out


def problems(wf, mock_url):
    p = list(v.lint(wf))
    urls = api_urls(wf)
    if not urls:
        p.append("no api_call tool: the bank services must be called through api_call tools")
    for where, name, url in urls:
        if not isinstance(url, str) or not url.startswith(mock_url.rstrip("/") + "/"):
            p.append(f"{where}/{name}: url default {url!r} must start with MOCK_BASE_URL {mock_url.rstrip('/')}/")
    if "{{" in json.dumps([n.get("pre_process_code", "") for n in (wf.get("nodes") or {}).values()]):
        pass   # lint already checks code for double braces
    return p


def build(case_path, mock_url, llm, trace, root, slot, max_rounds=3):
    docs, files = bi.read_brief(case_path, root / "out" / "inputs" / slot, max_file_chars=40000)
    trace.step(f"general builder path: {len(files)} case file(s), {len(docs)} chars of documents", files=files)
    kit = root / "kit"
    ref = "\n\n".join(f"=== FREYA DOC: {p} ===\n" + (kit / p).read_text() for p in ("docs/workflow-format.md", "docs/tools-and-code.md"))
    example = (kit / "examples/base-agent.json").read_text()
    prompt = (f"MOCK_BASE_URL: {mock_url.rstrip('/')}\n\n{ref}\n\n=== A VALID MINIMAL EXAMPLE WORKFLOW (format only) ===\n{example}\n\n"
              f"=== THE CASE DOCUMENTS ===\n{docs}\n\nWrite the complete workflow for this case.")
    out = llm.ask_json("write the phone-agent workflow from the case documents", GUIDE, prompt, SCHEMA, timeout_s=1200, effort="medium")
    wf, notes = out["workflow"], out.get("design_notes", "")
    trace.step("design notes from the construction model", notes=notes[:4000])
    for rnd in range(1, max_rounds + 1):
        probs = problems(wf, mock_url)
        trace.test(f"workflow checks (round {rnd})", "pass" if not probs else f"{len(probs)} problem(s)", problems=probs[:30])
        if not probs:
            return wf, notes
        if rnd == max_rounds:
            break
        out = llm.ask_json("repair the workflow so the checks pass", GUIDE, prompt + "\n\n=== YOUR PREVIOUS WORKFLOW ===\n" + json.dumps(wf) +
                           "\n\n=== PROBLEMS FOUND BY THE CHECKS (fix all, change nothing else) ===\n" + "\n".join(probs[:40]), SCHEMA, timeout_s=600, effort="medium")
        wf, notes = out["workflow"], out.get("design_notes", notes)
    raise SystemExit("the generated workflow still fails the checks after repair rounds")
