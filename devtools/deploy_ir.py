"""Compile a policy IR fixture with the builder's compiler and save it to a dev slot (development only).

    .venv/bin/python devtools/deploy_ir.py tests/fixtures/ir_public_ab.json dev-ab [--api kit/tools/openapi.json] [--set notes.enabled=false ...]
"""
import argparse, json, pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "kit"))
from builder import openapi_tools as ot, compile_workflow as cw, validate as v
import freya

ap = argparse.ArgumentParser(); ap.add_argument("ir"); ap.add_argument("slot"); ap.add_argument("--api", default=str(ROOT / "kit/tools/openapi.json"))
ap.add_argument("--set", nargs="*", default=[]); ap.add_argument("--no-save", action="store_true")
a = ap.parse_args()
ir = json.load(open(a.ir))
for kv in a.set:
    path, val = kv.split("=", 1); cur = ir; keys = path.split(".")
    for k in keys[:-1]: cur = cur[k]
    cur[keys[-1]] = json.loads(val)
api = ot.load(a.api); roles, probs = ot.infer_roles(api)
if probs: sys.exit(f"role problems: {probs}")
wf = cw.compile_workflow(ir, api, roles)
problems = v.lint(wf, api)
print("lint:", problems or "clean", v.stats(wf))
if problems: sys.exit(1)
out = ROOT / "out" / f"{a.slot}.workflow.json"; out.write_text(json.dumps(wf, indent=1))
if a.no_save: sys.exit(0)
print("dry run:", freya.validate_workflow(a.slot, wf))
r = freya.save_workflow(a.slot, wf)
if "version" not in r: r = freya.save_workflow(a.slot, wf)   # first create: update once so agent_config gets the event model
print(json.dumps(r))
