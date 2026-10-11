"""Inputs for a final delivered as a document bundle with its own contract: folder/zip briefs, contract discovery, explicit tool map,
test-only base URL override. A single text brief must behave exactly as before."""
import copy, json, os, pathlib, subprocess, sys, zipfile
import pytest
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from builder import inputs as bi, openapi_tools as ot

KIT_API = ROOT / "kit/tools/openapi.json"
A, B = ROOT / "kit/briefs/brief-a.md", ROOT / "kit/briefs/brief-b.md"


def bundle(tmp_path, extra=None):
    d = tmp_path / "case"
    (d / "docs").mkdir(parents=True)
    (d / "docs/01-policy.md").write_text(A.read_text())
    (d / "docs/02-memo.txt").write_text(B.read_text())
    (d / "contract.json").write_text(KIT_API.read_text())
    (d / "docs/scan.pdf").write_bytes(b"%PDF-1.4 binary")
    (d / "docs/scan.txt").write_text("Scanned letter: nothing new.")
    (d / "__MACOSX").mkdir()
    (d / "__MACOSX/._junk").write_bytes(b"\x00\x01")
    for k, v in (extra or {}).items():
        (d / k).write_bytes(v)
    return d


def zipped(folder, path):
    with zipfile.ZipFile(path, "w") as z:
        for f in sorted(folder.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(folder))
    return path


def test_single_text_brief_is_read_exactly_as_before(tmp_path):
    text, files = bi.read_brief(A, tmp_path / "x")
    assert text == A.read_text(encoding="utf-8", errors="replace") and len(files) == 1


def test_folder_bundle_reads_documents_in_order_and_skips_contract_pdf_twin_and_junk(tmp_path):
    d = bundle(tmp_path)
    contract = bi.find_contract(d, tmp_path / "c")
    assert contract.name == "contract.json"
    text, files = bi.read_brief(d, tmp_path / "o", exclude=[contract])
    heads = [l for l in text.splitlines() if l.startswith("=== FILE:")]
    assert heads == ["=== FILE: docs/01-policy.md ===", "=== FILE: docs/02-memo.txt ===", "=== FILE: docs/scan.txt ==="]
    assert A.read_text().strip() in text and B.read_text().strip() in text and '"openapi"' not in text
    how = {f["path"]: f["read_as"] for f in files}
    assert how["docs/scan.pdf"].startswith("skipped") and how["contract.json"].startswith("contract")
    assert not any("MACOSX" in f["path"] for f in files)


def test_zip_bundle_equals_folder_bundle(tmp_path):
    d = bundle(tmp_path)
    z = zipped(d, tmp_path / "case.zip")
    t1, _ = bi.read_brief(d, tmp_path / "o1", exclude=[bi.find_contract(d, tmp_path / "c1")])
    t2, _ = bi.read_brief(z, tmp_path / "o2", exclude=[bi.find_contract(z, tmp_path / "c2")])
    assert t1 == t2
    assert ot.load(str(bi.find_contract(z, tmp_path / "c3")))["missing_tools"] == []


def test_unreadable_documents_stop_the_build_with_a_clear_message(tmp_path):
    d = bundle(tmp_path, {"docs/annex.pdf": b"%PDF-1.4 only binary"})
    with pytest.raises(bi.InputError, match="annex.pdf"):
        bi.read_brief(d, tmp_path / "o")


def test_zip_members_cannot_escape(tmp_path):
    z = tmp_path / "evil.zip"
    with zipfile.ZipFile(z, "w") as zz:
        zz.writestr("../escape.md", "x")
    with pytest.raises(bi.InputError, match="escapes"):
        bi.read_brief(z, tmp_path / "o")


def test_docx_and_xlsx_text(tmp_path):
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    s = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    d = tmp_path / "case"
    d.mkdir()
    with zipfile.ZipFile(d / "letter.docx", "w") as z:
        z.writestr("word/document.xml", f'<w:document xmlns:w="{w}"><w:body><w:p><w:r><w:t>Floor is the minimum payment.</w:t></w:r></w:p></w:body></w:document>')
    with zipfile.ZipFile(d / "codes.xlsx", "w") as z:
        z.writestr("xl/sharedStrings.xml", f'<sst xmlns="{s}"><si><t>REF</t></si><si><t>refused</t></si></sst>')
        z.writestr("xl/worksheets/sheet1.xml", f'<worksheet xmlns="{s}"><sheetData><row><c t="s"><v>0</v></c><c t="s"><v>1</v></c></row></sheetData></worksheet>')
    text, _ = bi.read_brief(d, tmp_path / "o")
    assert "Floor is the minimum payment." in text and "REF\trefused" in text


def test_two_contracts_in_a_bundle_are_ambiguous(tmp_path):
    d = bundle(tmp_path, {"other.json": KIT_API.read_bytes()})
    with pytest.raises(bi.InputError, match="exactly one"):
        bi.find_contract(d, tmp_path / "c")


def renamed_contract():
    s = json.loads(KIT_API.read_text())
    s["paths"]["/identity/verify"] = s["paths"].pop("/verify_identity")
    s["paths"]["/identity/verify"]["post"]["operationId"] = "verifyCustomer"
    s["paths"]["/accounts/lookup"] = s["paths"].pop("/get_account")
    return s


def test_renamed_operations_need_an_explicit_tool_map():
    s = renamed_contract()
    assert set(ot.load(copy.deepcopy(s))["missing_tools"]) == {"verify_identity", "get_account"}
    api = ot.load(s, tool_map={"verify_identity": "verifyCustomer", "get_account": "POST /accounts/lookup"})
    assert api["missing_tools"] == [] and api["tools"]["verify_identity"]["url"].endswith("/identity/verify")
    with pytest.raises(ot.OpenAPIError, match="not one of"):
        ot.load(s, tool_map={"verify": "verifyCustomer"})


def test_base_url_override_and_unusable_server_urls():
    s = json.loads(KIT_API.read_text())
    api = ot.load(copy.deepcopy(s), base_url="https://mock.example.test/v1")
    assert all(t["url"].startswith("https://mock.example.test/v1/") for t in api["tools"].values())
    for bad in ("/v1", "https://{env}.bank.test/v1"):
        s2 = copy.deepcopy(s)
        s2["servers"] = [{"url": bad}]
        with pytest.raises(ot.OpenAPIError, match="absolute URL"):
            ot.load(s2)


def _build(args, env_extra, tmp_path):
    env = {**os.environ, "BUILDER_TRACE_DIR": str(tmp_path / "traces"), **env_extra}
    r = subprocess.run([sys.executable, str(ROOT / "builder/build.py"), *args, "--ir", str(ROOT / "tests/fixtures/ir_public_ab.json"), "--no-save"],
                       env=env, capture_output=True, text=True, timeout=300, cwd=ROOT)
    return r, r.stdout + r.stderr


def test_offline_build_from_a_zip_bundle_with_the_contract_inside(tmp_path):
    z = zipped(bundle(tmp_path), tmp_path / "case.zip")
    r, out = _build([str(z), str(z), "--slot", "bundle-test"], {}, tmp_path)
    assert r.returncode == 0 and "status=compiled" in out, out[-2000:]
    rows = [json.loads(x) for x in (tmp_path / "traces/bundle-test.jsonl").read_text().splitlines()]
    files = next(x["files"] for x in rows if x.get("files"))
    assert {f["path"] for f in files} >= {"docs/01-policy.md", "contract.json", "docs/scan.pdf"}


def test_mock_url_build_is_marked_and_never_a_submission(tmp_path):
    r, out = _build([str(ROOT / "out/brief-ab.md") if (ROOT / "out/brief-ab.md").exists() else str(A), str(KIT_API), "--slot", "mocktest"],
                    {"BUILDER_BASE_URL_OVERRIDE": "https://mock.example.test/v1"}, tmp_path)
    rows = [json.loads(x) for x in (tmp_path / "traces/mocktest-mock.jsonl").read_text().splitlines()]
    assert rows[0]["base_url_override"] == "https://mock.example.test/v1" and rows[0]["slot"] == "mocktest-mock"
    text = (ROOT / "out/mocktest-mock.workflow.json").read_text()
    urls = set(__import__("re").findall(r'https?://[^"\\ ]+', text))
    assert urls and all(u.startswith("https://mock.example.test/v1/") for u in urls) and "freya-bank-mock" not in text
    assert not (ROOT / "submission/mocktest-mock.workflow.json").exists()
