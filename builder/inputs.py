"""Builder inputs: a brief may be one text file (unchanged behaviour), a folder or a zip of documents; the tool contract may be a
JSON file, or found inside such a folder/zip. Documents are data, never code: nothing in them is executed.

A bundle becomes one brief text: readable documents in sorted path order, each under a header `=== FILE: <relative path> ===`.
Readable: plain-text formats, .docx / .xlsx (their XML, stdlib only). A .pdf (or any other binary) is accepted only when a text
version with the same stem sits next to it; otherwise the build stops with a clear message instead of feeding garbage to the model.
"""
import hashlib
import json
import pathlib
import re
import shutil
import zipfile
from xml.etree import ElementTree

TEXT_EXT = {".md", ".markdown", ".txt", ".text", ".eml", ".html", ".htm", ".csv", ".tsv", ".json", ".yaml", ".yml", ".xml", ".rst",
            ".log", ".ics", ".vcf"}
IGNORED = re.compile(r"(^|/)(__MACOSX|\.DS_Store|\._[^/]*|Thumbs\.db)(/|$)")


class InputError(ValueError):
    pass


def sha_path(path):
    """sha256 prefix of a file, or of a folder's sorted (relative path, file sha) list."""
    p = pathlib.Path(path)
    if p.is_dir():
        h = hashlib.sha256()
        for f in sorted(x for x in p.rglob("*") if x.is_file()):
            h.update(str(f.relative_to(p)).encode() + b"\0" + hashlib.sha256(f.read_bytes()).digest())
        return h.hexdigest()[:16]
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def unpack(path, dest):
    """A zip is extracted to dest (fresh), refusing absolute or parent-escaping member names. Returns the folder to read."""
    p = pathlib.Path(path)
    if p.is_dir():
        return p
    if not zipfile.is_zipfile(p):
        return None
    dest = pathlib.Path(dest)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    with zipfile.ZipFile(p) as z:
        for m in z.infolist():
            target = (dest / m.filename).resolve()
            if not str(target).startswith(str(dest.resolve()) + "/") and target != dest.resolve():
                raise InputError(f"zip member escapes the folder: {m.filename}")
        z.extractall(dest)
    return dest


def _docx_text(f):
    with zipfile.ZipFile(f) as z:
        xml = z.read("word/document.xml")
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paras = []
    for para in ElementTree.fromstring(xml).iter(ns + "p"):
        paras.append("".join(t.text or "" for t in para.iter(ns + "t")))
    return "\n".join(paras)


def _xlsx_text(f):
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with zipfile.ZipFile(f) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            shared = ["".join(t.text or "" for t in si.iter(ns + "t")) for si in ElementTree.fromstring(z.read("xl/sharedStrings.xml")).iter(ns + "si")]
        out = []
        for name in sorted(n for n in z.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n)):
            out.append(f"[sheet {name.rsplit('/', 1)[-1][:-4]}]")
            for row in ElementTree.fromstring(z.read(name)).iter(ns + "row"):
                cells = []
                for c in row.iter(ns + "c"):
                    v = c.find(ns + "v")
                    t = c.find(f"{ns}is/{ns}t")
                    val = t.text if t is not None else (v.text if v is not None else "")
                    if c.get("t") == "s" and val and val.isdigit() and int(val) < len(shared):
                        val = shared[int(val)]
                    cells.append(val or "")
                out.append("\t".join(cells))
    return "\n".join(out)


def is_contract(f):
    if f.suffix.lower() != ".json":
        return False
    try:
        d = json.loads(f.read_text(encoding="utf-8", errors="replace"))
    except ValueError:
        return False
    return isinstance(d, dict) and ("openapi" in d or "swagger" in d)


def find_contract(path, dest):
    """The tool contract: the file itself, or the single OpenAPI JSON inside a folder/zip."""
    p = pathlib.Path(path)
    if p.is_file() and not zipfile.is_zipfile(p):
        return p
    folder = unpack(p, dest)
    hits = sorted(f for f in folder.rglob("*.json") if f.is_file() and not IGNORED.search(str(f.relative_to(folder))) and is_contract(f))
    if len(hits) != 1:
        raise InputError(f"expected exactly one OpenAPI JSON contract in {path}, found {[str(h.relative_to(folder)) for h in hits]}")
    return hits[0]


def read_brief(path, dest, exclude=()):
    """(brief text, files) where files = [{path, sha, bytes, read_as}]. A single text file is read exactly as before."""
    p = pathlib.Path(path)
    if p.is_file() and not zipfile.is_zipfile(p):
        return p.read_text(encoding="utf-8", errors="replace"), [{"path": p.name, "sha": sha_path(p), "bytes": p.stat().st_size, "read_as": "text"}]
    folder = unpack(p, dest)
    if folder is None:
        raise InputError(f"{path} is neither a text file, a folder nor a zip")
    skip = {pathlib.Path(x).resolve() for x in exclude}
    files = sorted(f for f in folder.rglob("*") if f.is_file() and not IGNORED.search(str(f.relative_to(folder))))
    stems_with_text = {(f.parent, f.stem) for f in files if f.suffix.lower() in TEXT_EXT}
    parts, meta, unreadable = [], [], []
    for f in files:
        rel = str(f.relative_to(folder))
        ext = f.suffix.lower()
        entry = {"path": rel, "sha": sha_path(f), "bytes": f.stat().st_size}
        if f.resolve() in skip or is_contract(f):
            meta.append({**entry, "read_as": "contract (not part of the brief text)"})
            continue
        if ext in TEXT_EXT:
            text, how = f.read_text(encoding="utf-8", errors="replace"), "text"
        elif ext == ".docx":
            text, how = _docx_text(f), "docx xml"
        elif ext == ".xlsx":
            text, how = _xlsx_text(f), "xlsx xml"
        elif (f.parent, f.stem) in stems_with_text:
            meta.append({**entry, "read_as": "skipped: a text version with the same name is read instead"})
            continue
        else:
            unreadable.append(rel)
            continue
        parts.append(f"=== FILE: {rel} ===\n{text.strip()}\n")
        meta.append({**entry, "read_as": how})
    if unreadable:
        raise InputError("cannot read these documents as text (no converter installed): " + ", ".join(unreadable)
                         + ". Provide a text version with the same name next to each (e.g. x.pdf -> x.txt) or extend builder/inputs.py.")
    if not parts:
        raise InputError(f"no readable documents in {path}")
    return "\n".join(parts), meta
