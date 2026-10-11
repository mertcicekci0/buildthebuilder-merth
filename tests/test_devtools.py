import os, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "devtools"))
import redact, secret_scan

# Fixtures are synthetic: every key-shaped literal in this file carries secret_scan.SYNTHETIC_MARKER, so the pre-commit scan exempts it.
FREYA_FIXTURE = "fak_SYNTHETICTEST0123456789"
ANTHROPIC_FIXTURE = "sk-ant-api03-SYNTHETICTEST-xyzxyz"


def keylike(prefix):
    """An UNMARKED key-shaped value built at run time, to prove the scanner still flags real-looking keys."""
    return prefix + "Q" * 20


def test_redact_env_value_and_patterns(monkeypatch):
    monkeypatch.setenv("FREYA_BANK_TEAM_KEY", "fbt-abcdefghijklmnop")
    s = redact.redact(f"key {FREYA_FIXTURE} team fbt-abcdefghijklmnop auth Bearer abcdefghijklmnopqrstu {ANTHROPIC_FIXTURE}")
    assert "SYNTHETICTEST" not in s and "fbt-abc" not in s and "abcdefghijklmnopqrstu" not in s
    assert s.count("[REDACTED]") == 4


def test_describe_never_shows_value(monkeypatch):
    monkeypatch.setenv("FREYA_API_KEY", FREYA_FIXTURE)
    d = redact.describe("FREYA_API_KEY")
    assert "SYNTHETIC" not in d and f"length {len(FREYA_FIXTURE)}" in d


def test_secret_scan_exempts_only_marked_synthetic_fixtures():
    vals = {"FREYA_BANK_TEAM_KEY": "fbt-abcdefghijklmnop"}
    assert secret_scan.scan_text("x", "export FREYA_API_KEY=fak_your_key_here", vals) == []
    assert secret_scan.scan_text("x", f"{FREYA_FIXTURE} {ANTHROPIC_FIXTURE}", vals) == []
    assert secret_scan.scan_text("x", keylike("fak_"), vals)                       # unmarked Freya-shaped key: flagged
    assert secret_scan.scan_text("x", keylike("sk-ant-"), vals)                    # unmarked Anthropic-shaped key: flagged
    assert secret_scan.scan_text("x", f"{FREYA_FIXTURE} {keylike('fak_')}", vals)   # a marked fixture does not hide a real one
    assert secret_scan.scan_text("x", "team fbt-abcdefghijklmnop", vals)            # exact env value: always flagged


def test_real_secret_values_are_flagged_even_with_the_marker():
    vals = {"FREYA_API_KEY": FREYA_FIXTURE}                                          # if a real value ever contained the marker
    assert secret_scan.scan_text("x", f"leak {FREYA_FIXTURE}", vals)
