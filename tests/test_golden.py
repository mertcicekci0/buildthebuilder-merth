"""The compiled workflow for the kit API + public IR, and for the R1-R3 reference IRs with their APIs, must not change
unless a change is deliberate. Regenerated at the C10 merge: relative to 4baeea6 the kit output differs only by the W2 changes that tests/test_w2_policy.py enumerates against golden_w2_kit_workflow.json (4baeea6)."""
import hashlib, json, pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from builder import openapi_tools as ot, compile_workflow as cw, validate as v


def _compile(api_path, ir_path):
    api = ot.load(str(ROOT / api_path))
    roles, problems = ot.infer_roles(api)
    ir = {k: x for k, x in json.load(open(ROOT / ir_path)).items() if not k.startswith("_")}
    warnings = []
    wf = cw.compile_workflow(ir, api, roles, warnings=warnings)
    assert v.lint(wf, api) == []
    return json.dumps(wf, indent=1), problems, warnings


def test_kit_workflow_is_byte_identical_to_golden():
    text, problems, warnings = _compile("kit/tools/openapi.json", "tests/fixtures/ir_public_ab.json")
    assert problems == [] and warnings == []
    assert text == (ROOT / "tests/fixtures/golden_kit_workflow.json").read_text()


def test_variant_workflows_match_golden_hashes():
    golden = json.load(open(ROOT / "tests/fixtures/golden_variant_sha256.json"))
    for r in ("r1", "r2", "r3"):
        text, problems, _ = _compile(f"tests/variants/api-{r}.json", f"tests/variants/ir-{r}.ref.json")
        assert problems == [], (r, problems)
        assert hashlib.sha256(text.encode()).hexdigest() == golden[r], r
