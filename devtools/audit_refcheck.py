"""Mechanical completeness check of docs/RESOURCE_AUDIT.md's source data (no model calls).

    python devtools/audit_refcheck.py evidence/kit-audit/audit-wf_21c0c28f-cc0.json

Checks: (1) every supplied kit file (both copies, hidden files included, __pycache__ excluded) has exactly one entry with
lines_read == lines_total == its line count (+1 when the file lacks a final newline); (2) every `path:line[-line]` reference in
implemented_at / evidence resolves to an existing file with that many lines, searched in this repo and ../fh-c10 (C13/C14 line
numbers); (3) entries whose findings have no evidence or an explicitly unverified one are listed (reading != implementation).
"""
import json, pathlib, re, sys
ROOT = pathlib.Path(__file__).resolve().parent.parent
BASES = [ROOT, ROOT.parent / "fh-c10", ROOT.parent]
DL = pathlib.Path("/Users/mertcicekci/Downloads/build-the-builder 2")


def nlines(p):
    b = p.read_bytes()
    return b.count(b"\n") + (1 if b and not b.endswith(b"\n") else 0)


C13 = "a51f13a"   # the audit cites candidate C13 line numbers for builder/ and tests/ (fh-c10 HEAD has moved on)


class GitFile:
    def __init__(self, rel):
        import subprocess
        self.rel = rel
        r = subprocess.run(["git", "-C", str(ROOT.parent / "fh-c10"), "show", f"{C13}:{rel}"], capture_output=True)
        self.data = r.stdout if r.returncode == 0 else None

    def is_file(self):
        return self.data is not None

    def read_bytes(self):
        return self.data


def resolve(ref):
    ref = re.sub(r"^(/Users/mertcicekci/Documents/ChatGPT/)?(fh-c10/|freyahackathon/|fh/)", "", ref).lstrip("./")
    for cand in ([ref, "builder/" + ref] if not ref.startswith(("kit/", "docs/", "evidence/", "results/", "probes/", "devtools/")) else []):
        g = GitFile(cand)
        if g.is_file():
            return g
    for sub in ("", "evidence/live-builds-C13/", "evidence/", "tests/fixtures/"):
        for base in BASES:
            p = base / (sub + ref)
            if p.is_file():
                return p
    for base in BASES:
        p = base / ref
        if p.is_file():
            return p
    return None


audit = json.load(open(sys.argv[1]))
entries = {}
for e in audit["entries"]:
    p = e["path"].split("freyahackathon/")[-1]
    p = re.split(r";|\)| identical| \(", p)[0].strip()
    entries.setdefault(p, []).append(e)

problems, unsupported = [], []
supplied = sorted(str(f.relative_to(DL)) for f in DL.rglob("*") if f.is_file() and "__pycache__" not in f.parts)
for rel in supplied:
    kp = "kit/" + rel
    es = entries.get(kp, [])
    if len(es) != 1:
        problems.append(f"{kp}: {len(es)} entries")
        continue
    n = nlines(ROOT / kp)
    if not (es[0]["lines_read"] == es[0]["lines_total"] == n):
        problems.append(f"{kp}: read {es[0]['lines_read']}/{es[0]['lines_total']}, file has {n} lines")
    if (ROOT / kp).read_bytes() != (DL / rel).read_bytes():
        problems.append(f"{kp}: copies differ")
extra = [p for p in entries if p.startswith("kit/") and p[4:] not in supplied]
print(f"supplied files: {len(supplied)}; entries: {len(audit['entries'])}; extra non-kit entries: {[p for p in entries if not p.startswith('kit/')]}")

REF = re.compile(r"([A-Za-z0-9_./-]+\.(?:py|md|json|sh|txt|jsonl|toml|lock|example))(?::|\s+L)(\d+)(?:[-–](\d+))?")
checked = bad = 0
for e in audit["entries"]:
    for f in e["findings"]:
        for field in ("implemented_at", "evidence"):
            for m in REF.finditer(f.get(field) or ""):
                path, a, b = m.group(1), int(m.group(2)), int(m.group(3) or m.group(2))
                p = resolve(path)
                if p is None and "/" not in path:   # bare file name: unique match under kit/, builder/, devtools/, tests/, docs/
                    hits = [h for base in BASES[:2] for d in ("kit", "builder", "devtools", "tests", "docs", "kit/tools", "kit/docs", "kit/examples")
                            for h in [(base / d / path)] if h.is_file()]
                    p = hits[0] if hits else None
                checked += 1
                if p is None:
                    bad += 1; problems.append(f"unresolved ref {path}:{a} ({e['path'].split('/')[-1][:30]})")
                elif max(a, b) > nlines(p):
                    bad += 1; problems.append(f"ref beyond end {path}:{a}-{b} > {nlines(p)} lines")
        ev = (f.get("evidence") or "").lower()
        if not ev or re.search(r"unverified|not verified|never (run|executed|invoked)|untested|not tested", ev):
            unsupported.append((e["path"].split("freyahackathon/")[-1][:40], f.get("ref", ""), f["finding"][:110]))
print(f"code/doc references checked: {checked}; unresolved or out of range: {bad}")
for p in problems:
    print("PROBLEM", p)
print(f"findings whose evidence is missing or explicitly unverified: {len(unsupported)}")
for u in unsupported:
    print("UNSUPPORTED", " | ".join(u))
