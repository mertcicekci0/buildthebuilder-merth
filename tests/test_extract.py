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
