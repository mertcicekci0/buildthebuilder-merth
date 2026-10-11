"""Builder trace: plain-text lines on stdout (organisers tee it to traces/<brief>.log) plus a JSONL twin.

Required content (hackathon page): start and end time; every model call (model + one-line purpose); what was
created on Freya (workflow id, agent id, number of steps and tools); every test the builder ran and its result.
Every line passes through redact().
"""
import datetime
import json
import os
import pathlib
import re
import sys
import time

_SECRET_ENV = ("FREYA_API_KEY", "FREYA_BANK_TEAM_KEY", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")
_PATTERNS = [re.compile(r"fak_[A-Za-z0-9_\-]{12,}"), re.compile(r"sk-ant-[A-Za-z0-9_\-]{12,}")]


def redact(text):
    s = str(text)
    for k in _SECRET_ENV:
        v = os.environ.get(k)
        if v and len(v) >= 8:
            s = s.replace(v, "[REDACTED]")
    for p in _PATTERNS:
        s = p.sub("[REDACTED]", s)
    return s


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


class Trace:
    def __init__(self, jsonl_path=None, stream=sys.stdout, t0=None):
        self.t0 = t0 or time.time()   # the builder passes its process start (kept across a runtime re-exec)
        self.stream = stream
        self.jsonl = open(jsonl_path, "a") if jsonl_path else None
        self.model_calls = []

    def _emit(self, kind, text, **data):
        line = f"[{now_iso()} +{time.time() - self.t0:6.1f}s] {kind.upper():<8} {text}"
        print(redact(line), file=self.stream, flush=True)
        if self.jsonl:
            self.jsonl.write(redact(json.dumps({"at": now_iso(), "kind": kind, "text": text, **data}, default=str)) + "\n")
            self.jsonl.flush()

    def start(self, **inputs):
        self._emit("start", "builder run started " + " ".join(f"{k}={v}" for k, v in inputs.items()), **inputs)

    def step(self, text, **data):
        self._emit("step", text, **data)

    def model_call(self, model, purpose, **data):
        self.model_calls.append({"model": model, "purpose": purpose, **data})
        extra = " ".join(f"{k}={v}" for k, v in data.items() if k in ("seconds", "input_tokens", "output_tokens", "status", "backend", "error"))
        if data.get("rate_limit"):
            extra += " rate_limit=" + ",".join(f"{k}:{v}" for k, v in data["rate_limit"].items() if v is not None)
        self._emit("model", f"model={model} purpose=\"{purpose}\" {extra}", **data)

    def created(self, text, **data):
        self._emit("freya", text, **data)

    def test(self, name, result, **data):
        self._emit("test", f"{name}: {result}", **data)

    def warn(self, text, **data):
        self._emit("warn", text, **data)

    def end(self, status, **data):
        self._emit("end", f"builder run ended status={status} duration_s={time.time() - self.t0:.0f} model_calls={len(self.model_calls)}", **data)
        if self.jsonl:
            self.jsonl.close()
