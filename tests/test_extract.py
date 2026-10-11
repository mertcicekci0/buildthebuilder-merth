import copy, io, json, pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from builder import openapi_tools as ot, extract as ex, llm as llm_mod
from builder.trace import Trace

IR = {k: v for k, v in json.load(open(ROOT / "tests/fixtures/ir_public_ab.json")).items() if not k.startswith("_")}
BRIEF = (ROOT / "kit/briefs/brief-a.md").read_text() + "\n" + (ROOT / "kit/briefs/brief-b.md").read_text()

class FakeLLM:
    def __init__(self, answers): self.answers, self.calls = answers, []
    def ask_json(self, purpose, system, prompt, schema, **kw):
        self.calls.append(purpose)
        return self.answers.pop(0)

def test_extract_audit_and_repair_loop():
    api = ot.load(str(ROOT / "kit/tools/openapi.json")); roles, _ = ot.infer_roles(api)
    broken = copy.deepcopy(IR); broken["outcomes"]["promise"] = "promised"          # audit misses it, repair fixes it
    fake = FakeLLM([copy.deepcopy(IR), {"changes": [], "ir": broken}, copy.deepcopy(IR)])
    out = io.StringIO()
    ir, problems, changes = ex.extract(fake, BRIEF, api, roles, Trace(stream=out))
    assert problems == [] and ir["outcomes"]["promise"] == "promise"
    assert fake.calls == ["extract the policy IR from the brief and the tool API",
                          "audit the IR against the brief for omissions, wrong values and precedence",
                          "repair the IR so the deterministic checks pass"]
    assert "policy checks (round 2): pass" in out.getvalue()

def test_replay_backend_needs_no_model_and_traces(tmp_path, monkeypatch):
    monkeypatch.setenv("BUILDER_LLM_BACKEND", "replay"); monkeypatch.setenv("BUILDER_REPLAY_DIR", str(tmp_path))
    key = llm_mod._key("p", "s", "u", {"type": "object"})
    (tmp_path / f"{key}.json").write_text(json.dumps({"model": "claude-opus-5-5", "output": {"x": 1}}))
    out = io.StringIO(); t = Trace(stream=out)
    assert llm_mod.LLM(t).ask_json("p", "s", "u", {"type": "object"}) == {"x": 1}
    assert 'model=claude-opus-5-5 purpose="p"' in out.getvalue() and len(t.model_calls) == 1



def test_failed_and_skipped_model_calls_are_traced(tmp_path):
    import asyncio, time as _t
    from builder import llm as L
    from builder.trace import Trace
    tr = Trace(tmp_path / "t.jsonl", stream=open(tmp_path / "out.txt", "w"))
    m = L.LLM(tr, backend="agent_sdk")

    async def boom(*a, **k):
        raise L.LLMError("model call timed out after 30s")
    m._agent_sdk = boom
    try:
        m.ask_json("purpose one", "sys", "prompt", {"type": "object", "properties": {}})
    except L.LLMError:
        pass
    m.deadline = _t.time()            # no time left: the attempt is still traced
    try:
        m.ask_json("purpose two", "sys", "prompt", {"type": "object", "properties": {}})
    except L.LLMError:
        pass
    calls = [c for c in tr.model_calls]
    assert [c["status"] for c in calls] == ["failed", "skipped_no_time"]
    assert "timed out" in calls[0]["error"]


def test_trace_lists_aux_models_and_replaces_file_on_rerun(tmp_path):
    from builder.trace import Trace
    out = tmp_path / "o.txt"
    for _ in range(2):
        tr = Trace(tmp_path / "t.jsonl", stream=open(out, "w"))
        tr.start(x=1)
        tr.model_call("claude-opus-5-5", "p", status="success", model_usage={"claude-opus-5-5": {"inputTokens": 1, "outputTokens": 2},
                                                                              "claude-haiku-5-5": {"inputTokens": 3, "outputTokens": 4}})
        tr.end("ok")
    assert open(out).read().count("aux_model=claude-haiku-5-5(in=3,out=4)") == 1
    assert sum(1 for l in open(tmp_path / "t.jsonl") if '"kind": "start"' in l) == 1


def test_check_warnings_are_traced_and_never_repaired():
    api = ot.load(str(ROOT / "kit/tools/openapi.json")); roles, _ = ot.infer_roles(api)
    odd = copy.deepcopy(IR); odd["calendar"]["holidays"] = odd["calendar"]["holidays"] + ["2026-12-31"]   # not in the brief
    fake = FakeLLM([copy.deepcopy(odd), {"changes": [], "ir": copy.deepcopy(odd)}])
    out = io.StringIO()
    ir, problems, _ = ex.extract(fake, BRIEF, api, roles, Trace(stream=out))
    assert problems == [] and "2026-12-31" in ir["calendar"]["holidays"] and len(fake.calls) == 2   # no repair call
    assert "holiday 2026-12-31 was not found written in the brief" in out.getvalue()
