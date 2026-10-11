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

    def ask_json(self, purpose, system, prompt, schema, timeout_s=600, effort=None):
        if self.deadline and time.time() + 30 > self.deadline:
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
        out, meta = asyncio.run(self._agent_sdk(system, prompt, schema, timeout_s, effort))
        meta.update(backend="agent_sdk", seconds=round(time.time() - t0, 1), key=key)
        self.trace.model_call(meta.pop("model", self.model), purpose, **meta)
        if self.record_dir:
            self.record_dir.mkdir(parents=True, exist_ok=True)
            (self.record_dir / f"{key}.json").write_text(json.dumps({"purpose": purpose, "model": self.model, "output": out}, indent=1))
        return out

    async def _agent_sdk(self, system, prompt, schema, timeout_s, effort):
        from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, query
        opts = ClaudeAgentOptions(
            model=self.model, system_prompt=system, tools=[], allowed_tools=[], setting_sources=[],
            permission_mode="dontAsk", max_turns=6, output_format={"type": "json_schema", "schema": schema},
            env={"CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION": "false", "DISABLE_AUTOUPDATER": "1", "CLAUDECODE": ""},
            **({"effort": effort} if effort else {}))
        models, result = set(), None

        async def run():
            nonlocal result
            async for m in query(prompt=prompt, options=opts):
                if isinstance(m, AssistantMessage) and getattr(m, "model", None):
                    models.add(m.model)
                elif isinstance(m, ResultMessage):
                    result = m
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
                "cost_estimate_usd": result.total_cost_usd}
        return result.structured_output, meta
