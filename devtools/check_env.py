"""Check the development environment without printing any secret.

    source env.sh && .venv/bin/python devtools/check_env.py            # local checks + read-only Freya check
    ... devtools/check_env.py --bank                                     # also opens one practice session (PUB-01) on the bank mock

Read-only by default: the only network call is GET /api/v2/workspaces on Freya.
Claude access is checked with `claude auth status` of the Agent SDK's bundled CLI: no model call, no cost.
"""
import argparse
import importlib.metadata as md
import os
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "kit"))
sys.path.insert(0, str(ROOT / "kit" / "tools"))
sys.path.insert(0, str(ROOT / "devtools"))
from redact import describe, redact  # noqa: E402


def line(ok, text):
    print(("  ok   " if ok else "  FAIL ") + text)
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bank", action="store_true", help="create one practice session for PUB-01 on the bank mock")
    a = ap.parse_args()
    good = True

    print("Local")
    good &= line(sys.version_info >= (3, 10), f"python {sys.version.split()[0]} at {sys.executable}")
    for pkg in ("claude-agent-sdk", "jsonschema", "pytest"):
        try:
            print(f"  ok   {pkg} {md.version(pkg)}")
        except md.PackageNotFoundError:
            good &= line(False, f"{pkg} not installed (run: uv sync)")
    kit_ok = (ROOT / "kit" / "VERSION").read_text().strip() == "3"
    good &= line(kit_ok, "kit/ present, VERSION 3")
    good &= line(shutil.which("claude") is not None, f"claude CLI: {shutil.which('claude') or 'not found'}")

    print("Secrets (presence only)")
    for name in ("FREYA_API_KEY", "FREYA_BANK_TEAM_KEY", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"):
        print("  " + describe(name))
    team = os.environ.get("FREYA_BANK_TEAM_KEY", "")
    good &= line(len(team) >= 16, "bank team key has 16+ characters")
    env_file = ROOT / "env.sh"
    if env_file.exists():
        mode = oct(env_file.stat().st_mode & 0o777)
        good &= line(mode == "0o600", f"env.sh permissions {mode}")
        tracked = subprocess.run(["git", "ls-files", "--error-unmatch", "env.sh"], cwd=ROOT, capture_output=True).returncode == 0
        good &= line(not tracked, "env.sh is not tracked by git")

    print("Freya platform")
    if not os.environ.get("FREYA_API_KEY"):
        good &= line(False, "FREYA_API_KEY missing: skipped")
    else:
        import freya  # reads the key at import time
        try:
            r = freya.api("GET", "/api/v2/workspaces")
            items = r.get("workspaces") or r.get("items") or r
            line(True, f"GET /api/v2/workspaces -> {len(items)} workspace(s); first id {str(items[0].get('id'))[:8]}...")
            print(f"       agent model fixed by kit: {freya.AGENT_LLM}")
        except Exception as e:  # noqa: BLE001
            good &= line(False, "workspace check failed: " + redact(e)[:300])

    if a.bank:
        print("Bank mock (creates one practice session)")
        from score import http
        try:
            r = http("/v1/practice/sessions", {"case": "PUB-01"})
            v = r.get("variables") or {}
            good &= line("customer_id" in v, f"practice session for PUB-01: variables {sorted(v)}")
        except Exception as e:  # noqa: BLE001
            good &= line(False, "bank session failed: " + redact(e)[:300])

    print("Builder model access (no model call)")
    try:
        import claude_agent_sdk
        cli = pathlib.Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude"
        env = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}
        out = subprocess.run([str(cli), "auth", "status"], capture_output=True, text=True, env=env, timeout=30)
        import json as _json
        st = _json.loads(out.stdout or "{}")
        good &= line(bool(st.get("loggedIn")), f"bundled CLI auth: method={st.get('authMethod')} subscription={st.get('subscriptionType')} provider={st.get('apiProvider')}")
        if os.environ.get("ANTHROPIC_API_KEY"):
            print("  note ANTHROPIC_API_KEY is set: it overrides a subscription login and bills per token")
    except Exception as e:  # noqa: BLE001
        good &= line(False, "could not read Claude auth status: " + redact(e)[:200])

    print("\nRESULT:", "all checks passed" if good else "some checks failed (see above)")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
