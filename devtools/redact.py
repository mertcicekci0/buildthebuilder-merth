"""Scrub secrets from text before it is printed, logged or written to a trace.

Every log line, trace record and saved transcript in this repo goes through redact().
"""
import os
import re

SECRET_ENV = ("FREYA_API_KEY", "FREYA_BANK_TEAM_KEY", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")

_PATTERNS = [
    re.compile(r"fak_[A-Za-z0-9_\-]{12,}"),
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{12,}"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9_\-\.]{12,}"),
    re.compile(r"(?i)(x-team-key['\"]?\s*[:=]\s*['\"]?)[^\s'\",}]{8,}"),
]


def _secret_values():
    return [v for k in SECRET_ENV if (v := os.environ.get(k)) and len(v) >= 8]


def redact(text):
    s = text if isinstance(text, str) else str(text)
    for v in _secret_values():
        s = s.replace(v, "[REDACTED]")
    for p in _PATTERNS:
        s = p.sub(lambda m: (m.group(1) if p.groups else "") + "[REDACTED]", s)
    return s


def describe(name):
    """Presence summary of a secret environment variable. Never prints the value."""
    v = os.environ.get(name)
    if not v:
        return f"{name}: MISSING"
    prefix = f", prefix {v[:4]}" if name == "FREYA_API_KEY" else ""
    return f"{name}: set (length {len(v)}{prefix})"
