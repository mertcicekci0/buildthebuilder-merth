"""Fail when staged changes or given paths contain secrets.

    python devtools/secret_scan.py --staged          # git pre-commit hook
    python devtools/secret_scan.py submission/       # before handing in traces and exports

Checks key-shaped strings and the literal values of the secrets in env.sh / the environment.
Prints only file names, never the matched value.
"""
import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
NAMES = ("FREYA_API_KEY", "FREYA_BANK_TEAM_KEY", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")
PATTERNS = {"Freya key": re.compile(r"fak_[A-Za-z0-9_\-]{12,}"), "Anthropic key": re.compile(r"sk-ant-[A-Za-z0-9_\-]{12,}")}
PLACEHOLDERS = ("fak_your_key_here", "sk-ant-...")
# Narrow exception for test fixtures: a key-shaped string that contains this marker is synthetic (a real, randomly generated key never
# contains the word). Exact values of the real secrets (env.sh / environment) are always reported, marker or not.
SYNTHETIC_MARKER = "SYNTHETICTEST"


def secret_values():
    vals = {}
    env_file = ROOT / "env.sh"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            m = re.match(r"\s*export\s+(" + "|".join(NAMES) + r"|[A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASS(?:WORD)?)[A-Z0-9_]*)=['\"]?([^'\"\s]+)", line)
            if m and len(m.group(2)) >= 8:
                vals[m.group(1)] = m.group(2)
    for k in list(NAMES) + [n for n in os.environ if re.search(r"(KEY|TOKEN|SECRET|PASS)", n) and n not in NAMES]:
        v = os.environ.get(k)
        if v and len(v) >= 8:
            vals[k] = v
    return vals


def scan_text(name, text, vals):
    hits = []
    cleaned = text
    for ph in PLACEHOLDERS:
        cleaned = cleaned.replace(ph, "")
    for label, pattern in PATTERNS.items():
        if any(SYNTHETIC_MARKER not in m.group(0) for m in pattern.finditer(cleaned)):
            hits.append(f"{name}: contains a {label}-shaped string")
    for k, v in vals.items():
        if v in text:
            hits.append(f"{name}: contains the value of {k}")
    return hits


def main(argv):
    vals, hits = secret_values(), []
    if argv[:1] == ["--staged"]:
        files = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
                               cwd=ROOT, capture_output=True, text=True).stdout.split()
        for f in files:
            if pathlib.Path(f).name == "env.sh":
                hits.append(f"{f}: env.sh must never be committed")
                continue
            blob = subprocess.run(["git", "show", f":{f}"], cwd=ROOT, capture_output=True).stdout
            hits += scan_text(f, blob.decode("utf-8", "replace"), vals)
    else:
        for arg in argv:
            p = pathlib.Path(arg)
            for x in ([y for y in p.rglob("*") if y.is_file()] if p.is_dir() else [p]):
                hits += scan_text(str(x), x.read_text(errors="replace"), vals)
    for h in hits:
        print("SECRET?", h)
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
