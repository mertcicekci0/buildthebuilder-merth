"""The builder's only model boundary: ask_json(purpose, system, prompt, schema) -> dict.

Backends (env BUILDER_LLM_BACKEND):
  agent_sdk (default) - Claude Agent SDK; its bundled Claude Code CLI uses the runner's own credential
                        (claude.ai Pro/Max/Team/Enterprise login, CLAUDE_CODE_OAUTH_TOKEN, or ANTHROPIC_API_KEY).
                        No tools, no project/user settings, structured output validated against `schema`.
  replay              - answers from recorded files (BUILDER_REPLAY_DIR); for offline tests. No model call.
Set BUILDER_RECORD_DIR to save every agent_sdk answer for later replay.
Every call is written to the trace with model, one-line purpose, timing and token usage.
"""
import asyncio
import hashlib
import json
import os
import pathlib
import time


class LLMError(RuntimeError):
    pass


def _key(purpose, system, prompt, schema):
    return hashlib.sha256(json.dumps([purpose, system, prompt, schema], sort_keys=True).encode()).hexdigest()[:24]


class LLM:
    def __init__(self, trace, model=None, backend=None, deadline=None):
        self.trace = trace
        self.model = model or os.environ.get("BUILDER_MODEL", "claude-opus-5-5")
        self.backend = backend or os.environ.get("BUILDER_LLM_BACKEND", "agent_sdk")
        self.deadline = deadline  # absolute time.time() after which no new call starts
        self.replay_dir = pathlib.Path(os.environ.get("BUILDER_REPLAY_DIR", "replay"))
        rec = os.environ.get("BUILDER_RECORD_DIR")
        self.record_dir = pathlib.Path(rec) if rec else None
        self.rate_limit = None   # last rate-limit state the CLI reported (status, window, utilization, reset time)

    def ask_json(self, purpose, system, prompt, schema, timeout_s=600, effort=None):
        """Every attempt is traced: successes, failures, timeouts and calls skipped for lack of time."""
        if self.deadline and time.time() + 30 > self.deadline:
            self.trace.model_call(self.model, purpose, backend=self.backend, status="skipped_no_time", seconds=0)
            raise LLMError(f"no time left for model call: {purpose}")
        if self.deadline:
            timeout_s = max(30, min(timeout_s, self.deadline - time.time() - 15))
        key = _key(purpose, system, prompt, schema)
        t0 = time.time()
        if self.backend == "replay":
            f = self.replay_dir / f"{key}.json"
            if not f.exists():
                raise LLMError(f"no recorded answer for {purpose} ({f})")
            rec = json.loads(f.read_text())
            self.trace.model_call(rec.get("model", self.model), purpose, backend="replay", seconds=0, status="replayed", key=key)
            return rec["output"]
        try:
            out, meta = asyncio.run(self._agent_sdk(system, prompt, schema, timeout_s, effort))
        except Exception as e:  # noqa: BLE001  traced, then re-raised to the caller (bounded retry lives in extract.py)
            self.trace.model_call(self.model, purpose, backend="agent_sdk", status="failed", seconds=round(time.time() - t0, 1), key=key,
                                  error=f"{type(e).__name__}: {str(e)[:300]}", rate_limit=self.rate_limit)
            raise
        except BaseException as e:  # the builder's hard time limit interrupted the call
            self.trace.model_call(self.model, purpose, backend="agent_sdk", status="interrupted", seconds=round(time.time() - t0, 1), key=key,
                                  error=type(e).__name__)
            raise
        meta.update(backend="agent_sdk", seconds=round(time.time() - t0, 1), key=key, rate_limit=self.rate_limit)
        meta["session_log"] = self._keep_session(meta.get("session_id"))
        self.trace.model_call(meta.pop("model", self.model), purpose, **meta)
        if self.record_dir:
            self.record_dir.mkdir(parents=True, exist_ok=True)
            (self.record_dir / f"{key}.json").write_text(json.dumps({"purpose": purpose, "model": self.model, "output": out}, indent=1))
        return out

    def _keep_session(self, sid):
        """Copy this call's Claude Code session transcript (the builder agent's own session log) next to the trace, redacted."""
        import glob
        import os
        import pathlib
        dest = os.environ.get("BUILDER_SESSION_DIR")
        if not sid or not dest:
            return None
        hits = glob.glob(os.path.expanduser(f"~/.claude/projects/*/{sid}.jsonl"))
        if not hits:
            return None
        from builder.trace import redact
        out = pathlib.Path(dest) / f"{sid}.jsonl"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(redact(pathlib.Path(hits[0]).read_text(encoding="utf-8", errors="replace")))
        return str(out)

    async def _agent_sdk(self, system, prompt, schema, timeout_s, effort):
        from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, query
        try:
            from claude_agent_sdk import RateLimitEvent
        except ImportError:  # older SDKs
            RateLimitEvent = ()
        opts = ClaudeAgentOptions(
            model=self.model, system_prompt=system, tools=[], allowed_tools=[], setting_sources=[],
            permission_mode="dontAsk", max_turns=6, output_format={"type": "json_schema", "schema": schema},
            env={"CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION": "false", "DISABLE_AUTOUPDATER": "1", "CLAUDECODE": "",
                 "DISABLE_NON_ESSENTIAL_MODEL_CALLS": "1"},
            **({"effort": effort} if effort else {}))
        models, result = set(), None

        async def run():
            nonlocal result
            async for m in query(prompt=prompt, options=opts):
                if isinstance(m, AssistantMessage) and getattr(m, "model", None):
                    models.add(m.model)
                elif isinstance(m, ResultMessage):
                    result = m
                elif RateLimitEvent and isinstance(m, RateLimitEvent):
                    i = m.rate_limit_info
                    self.rate_limit = {"status": i.status, "window": i.rate_limit_type, "utilization": i.utilization, "resets_at": i.resets_at}
        try:
            await asyncio.wait_for(run(), timeout=timeout_s)
        except asyncio.TimeoutError:
            raise LLMError(f"model call timed out after {timeout_s:.0f}s") from None
        if result is None or result.is_error or not result.structured_output:
            raise LLMError(f"no structured output (subtype={getattr(result, 'subtype', None)}, errors={getattr(result, 'errors', None)})")
        usage = result.usage or {}
        meta = {"model": ",".join(sorted(models)) or self.model, "status": result.subtype, "turns": result.num_turns,
                "input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens"),
                "cache_read_tokens": usage.get("cache_read_input_tokens"), "model_usage": result.model_usage,
                "cost_estimate_usd": result.total_cost_usd, "session_id": getattr(result, "session_id", None)}
        return result.structured_output, meta
