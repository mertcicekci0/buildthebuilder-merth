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
        seen = {}
        for m in z.infolist():
            target = (dest / m.filename).resolve()
            if not str(target).startswith(str(dest.resolve()) + "/") and target != dest.resolve():
                raise InputError(f"zip member escapes the folder: {m.filename}")
            if (m.external_attr >> 16) & 0o170000 == 0o120000:
                raise InputError(f"zip member is a symbolic link: {m.filename}")
            key = m.filename.rstrip("/").casefold()
            if not m.is_dir() and key in seen:   # duplicates or case-only differences would overwrite each other on macOS
                raise InputError(f"zip members collide: {seen[key]!r} and {m.filename!r}")
            seen[key] = m.filename
        z.extractall(dest)
    return dest


def _docx_text(f):
    with zipfile.ZipFile(f) as z:
        xml = z.read("word/document.xml")
    root = ElementTree.fromstring(xml)
    ns = "{" + root.tag[1:].split("}")[0] + "}" if root.tag.startswith("{") else ""   # transitional or strict namespace

    def para_text(p):
        out = []
        for el in p.iter():
            if el.tag == ns + "t":
                out.append(el.text or "")
            elif el.tag == ns + "tab":
                out.append("\t")
            elif el.tag in (ns + "br", ns + "cr"):
                out.append("\n")
        return "".join(out)

    lines = []
    body = root.find(ns + "body")
    for block in (body if body is not None else root):
        if block.tag == ns + "tbl":   # one line per table row, cells separated by ' | '
            for tr in block.iter(ns + "tr"):
                lines.append(" | ".join(" ".join(para_text(p) for p in tc.iter(ns + "p")).strip() for tc in tr.iter(ns + "tc")))
        elif block.tag == ns + "p":
            lines.append(para_text(block))
    return "\n".join(lines)


def _xlsx_text(f):
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with zipfile.ZipFile(f) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            shared = ["".join(t.text or "" for t in si.iter(ns + "t")) for si in ElementTree.fromstring(z.read("xl/sharedStrings.xml")).iter(ns + "si")]
        out = []
        sheets = [n for n in z.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n)]
        for name in sorted(sheets, key=lambda n: int(re.search(r"(\d+)\.xml$", n).group(1))):
            out.append(f"[sheet {name.rsplit('/', 1)[-1][:-4]}]")
            for row in ElementTree.fromstring(z.read(name)).iter(ns + "row"):
                cells = {}
                for i, c in enumerate(row.iter(ns + "c")):
                    m = re.match(r"([A-Z]+)", c.get("r") or "")
                    col = (sum((ord(ch) - 64) * 26 ** k for k, ch in enumerate(reversed(m.group(1)))) - 1) if m else i
                    v = c.find(ns + "v")
                    t = c.find(f"{ns}is/{ns}t")
                    val = t.text if t is not None else (v.text if v is not None else "")
                    if c.get("t") == "s" and val and val.isdigit() and int(val) < len(shared):
                        val = shared[int(val)]
                    cells[col] = val or ""
                if cells:   # empty cells are padded so values stay under their column; numbers (incl. dates) stay as stored
                    out.append("\t".join(cells.get(k, "") for k in range(max(cells) + 1)))
    return "\n".join(out)


PDF_SWIFT = """import PDFKit
import Foundation
var out: [String: String] = [:]
for p in CommandLine.arguments.dropFirst() {
    if let d = PDFDocument(url: URL(fileURLWithPath: p)) { out[p] = d.string ?? "" } else { out[p] = "\\u{0}UNREADABLE" }
}
let data = try! JSONSerialization.data(withJSONObject: out)
print(String(data: data, encoding: .utf8)!)
"""


def pdf_texts(files):
    """{path: text} for PDFs via macOS PDFKit (swift), one process for all files. Raises InputError when unavailable or empty."""
    import shutil as _sh
    import subprocess
    files = [str(f) for f in files]
    if not files:
        return {}
    swift = _sh.which("swift")
    if not swift:
        raise InputError("PDF documents need a converter (macOS swift/PDFKit not found): " + ", ".join(pathlib.Path(f).name for f in files))
    r = subprocess.run([swift, "-e", PDF_SWIFT, *files], capture_output=True, timeout=240)
    try:
        got = json.loads(r.stdout.decode("utf-8", "replace").strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise InputError(f"PDF text extraction failed: {r.stderr.decode('utf-8', 'replace')[-300:]}")
    for f in files:
        t = got.get(f, "")
        if t.startswith("\x00UNREADABLE") or not t.strip():
            raise InputError(f"{pathlib.Path(f).name}: no text layer could be read (scanned or protected PDF?); provide a text version")
    return {f: got[f] for f in files}


def _read_document(f, rel):
    ext = f.suffix.lower()
    if ext == ".pdf":
        return pdf_texts([f])[str(f)], "pdf (PDFKit)"
    if ext not in (".docx", ".xlsx"):
        raise InputError(f"cannot read {rel} as text (no converter installed); provide a text version or extend builder/inputs.py")
    try:
        text = _docx_text(f) if ext == ".docx" else _xlsx_text(f)
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError, ValueError) as e:
        raise InputError(f"cannot read {rel} as {ext}: {type(e).__name__}: {e}")
    if not text.strip():
        raise InputError(f"{rel}: no text found in the {ext} file")
    return text, ext[1:] + " xml"


def _yaml_to_json(f, out_dir):
    """A YAML contract converted once to JSON with Ruby's YAML.safe_load (no code execution; this repo has no YAML library).
    Returns the JSON path, or raises InputError."""
    import shutil as _sh
    import subprocess
    ruby = _sh.which("ruby")
    if not ruby:
        raise InputError(f"{f.name} is YAML and no converter is available (no ruby, no PyYAML): convert it to JSON")
    r = subprocess.run([ruby, "-ryaml", "-rjson", "-rdate", "-e",
                        "puts JSON.generate(YAML.safe_load(STDIN.read, [Date, Time], [], true))"],
                       input=f.read_bytes(), capture_output=True, timeout=60)
    if r.returncode != 0:
        raise InputError(f"cannot convert {f.name} from YAML: {r.stderr.decode('utf-8', 'replace')[:300]}")
    out = pathlib.Path(out_dir) / (f.stem + ".converted.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(r.stdout)
    return out


def _yaml_contract(f):
    return f.suffix.lower() in (".yaml", ".yml") and bool(re.search(r"(?m)^(openapi|swagger)\s*:", f.read_text(encoding="utf-8", errors="replace")))


def is_contract(f):
    if _yaml_contract(f):
        return True
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
        return _yaml_to_json(p, pathlib.Path(dest).parent / (pathlib.Path(dest).name + "-converted")) if _yaml_contract(p) else p
    folder = unpack(p, dest)
    hits = sorted(f for f in folder.rglob("*") if f.is_file() and f.suffix.lower() in (".json", ".yaml", ".yml")
                  and not IGNORED.search(str(f.relative_to(folder))) and is_contract(f))
    if len(hits) != 1:
        raise InputError(f"expected exactly one OpenAPI contract (JSON or YAML) in {path}, found {[str(h.relative_to(folder)) for h in hits]}")
    if _yaml_contract(hits[0]):   # the converted copy sits outside the extracted folder, so the brief reader never sees it
        return _yaml_to_json(hits[0], pathlib.Path(str(folder) + "-converted"))
    return hits[0]


def read_brief(path, dest, exclude=()):
    """(brief text, files) where files = [{path, sha, bytes, read_as}]. A single text file is read exactly as before."""
    p = pathlib.Path(path)
    if p.is_file() and (p.suffix.lower() in (".docx", ".xlsx", ".pdf")
                        or (not zipfile.is_zipfile(p) and b"\x00" in p.read_bytes()[:8192])):   # binary: never fed to the model as text
        text, how = _read_document(p, p.name)   # a single office document (or an unreadable binary: clear stop)
        return text, [{"path": p.name, "sha": sha_path(p), "bytes": p.stat().st_size, "read_as": how}]
    if p.is_file() and not zipfile.is_zipfile(p):
        return p.read_text(encoding="utf-8", errors="replace"), [{"path": p.name, "sha": sha_path(p), "bytes": p.stat().st_size, "read_as": "text"}]
    folder = unpack(p, dest)
    if folder is None:
        raise InputError(f"{path} is neither a text file, a folder nor a zip")
    skip = {pathlib.Path(x).resolve() for x in exclude}
    files = sorted(f for f in folder.rglob("*") if f.is_file() and not IGNORED.search(str(f.relative_to(folder))))
    stems_with_text = {(f.parent, f.stem) for f in files if f.suffix.lower() in TEXT_EXT}
    pdfs = [f for f in files if f.suffix.lower() == ".pdf" and (f.parent, f.stem) not in stems_with_text]
    pdf_text = pdf_texts(pdfs) if pdfs else {}   # one swift process for every PDF of the bundle
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
        elif ext in (".docx", ".xlsx"):
            text, how = _read_document(f, rel)
        elif str(f) in pdf_text:
            text, how = pdf_text[str(f)], "pdf (PDFKit)"
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
