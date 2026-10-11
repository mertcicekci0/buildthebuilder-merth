# Builder model access: what is required, what was a choice

Checked 2026-10-10 ~23:45 Istanbul. Sources: hackathon page (live, Last-Modified 2026-10-10 13:52 GMT), organiser email, kit v3, kickoff auto-transcript, Anthropic docs fetched the same evening (code.claude.com: authentication, legal-and-compliance, agent-sdk/overview, cli-reference).

## Answers

**1. Which component needs model access?**
- Our development session: Claude Code on Mert's Team login (already working).
- The submitted builder: only if it calls a model. Ours does, for reading the brief.
- The Freya phone agent: runs on Freya's model (`experimental-5`) on Freya's servers. It needs no Anthropic access. Source: page, "Every phone agent runs on the same Freya model"; kit `freya.py` `AGENT_LLM`.

**2. Why did I propose the Messages API?** It was an implementation choice, not a requirement. My reasons were per-call `timeout`/`max_tokens` control, an exact per-call trace, and a misreading of Anthropic's policy (see "Correction" below). The Agent SDK covers what we need:
- `output_format` JSON schema with a validated `structured_output`;
- `max_turns` and `max_budget_usd` limits;
- per-message `model` and `usage`, plus per-model `model_usage` for the trace.

It runs on the existing subscription. Verified with no model call: `claude auth status` of the SDK's bundled CLI 2.1.294 reports `authMethod: claude.ai`, `subscriptionType: team`.

**3. Do organisers provide model credentials, credits, an endpoint or an environment?**
- Builder: no. Every source is silent, and the page says "Models the builder may use: Any. You pay for your builder's own model calls."
- Freya's own testing capacity: yes. The organisers provide the Freya workspace, the phone-agent model on "new GPU servers", and the mock bank.
- The kickoff mention of a "$10 budget ... we will increase" comes right before "We specially created a new GPU servers for you guys". My inference is that it means Freya testing capacity, not builder credits. That reading is ambiguous and unconfirmed.

**4. Whose credentials during the top-10 rebuild?** Unspecified.
- The page says only that they "clone the repository at that commit, run the builder ourselves on the same briefs and compare with your trace".
- The kit's example builder states its own requirement as "a Claude login or `ANTHROPIC_API_KEY`" (README.md:36, examples/agent_builder.py:8). That implies the rebuilding party supplies their own Claude access.
- With the Agent SDK, any of these works without Mert's personal login: their own Claude Code login, `CLAUDE_CODE_OAUTH_TOKEN`, an API key, or Bedrock/Vertex/Foundry.

**5. Is participant-funded API usage required?** No. It is optional; the page asks only that each team pay for its own builder's model calls.
- "Pay for" does not say "with an API key". The kit's own example builder runs on a Claude login.
- A participant can meet this with an existing subscription.

## Correction (repo claim was wrong)
README previously said: "subscription logins may not be used by third-party programs per Anthropic policy". Current docs do not say that for our situation:
- legal-and-compliance: "OAuth authentication is intended exclusively for purchasers of Claude Free, Pro, Max, Team, and Enterprise subscription plans and is designed to support ordinary use of Claude Code and other native Anthropic applications". Also: "Advertised usage limits for Pro and Max plans assume ordinary, individual usage of Claude Code **and the Agent SDK**".
- The prohibition targets developers who **offer** claude.ai login to their users or "route requests through Free, Pro, or Max plan credentials **on behalf of their users**". It "does not ... prevent an end user from signing in to the unmodified Claude Code binary with their own Claude subscription".
- agent-sdk/overview: "Unless previously approved, Anthropic does not allow third party developers to offer claude.ai login or rate limits for their products". That is about products offered to others.

Our case: Mert runs his own builder with his own Team login, and organisers rerun it with their own credentials. Nobody's usage is routed through someone else's subscription. **Residual uncertainty:** whether a hackathon builder counts as a "product". The page's own example builder assumes a Claude login is fine. Mert's Team terms apply (Commercial Terms for Team/Enterprise); a Team admin may also have enabled paid extra usage, which would bill beyond the included limits.

## Recommendation
- Builder model calls go through the Claude Agent SDK, using whatever Claude Code credential the runner has. We set no API key; `ANTHROPIC_API_KEY` would override the login and bill per token.
- The model boundary is one small module (`builder/llm.py`) with two backends:
  - `agent_sdk`, the default, running on the subscription;
  - `replay`, which reads recorded responses, for offline tests and reproducible development.
- An API-key backend can be added in about 30 lines if ever needed.
- Keep model calls few and bounded, about 3–5 per build. The deterministic parts (OpenAPI parsing, dates, amounts, compiling, validation) use no model at all.

## Draft question to organisers (not sent)
> For the top-10 rebuild: whose Claude/model access do you use when you run our builder? Our builder calls Claude through the Claude Agent SDK, which uses whatever Claude Code login or key the runner has, exactly like the kit's example builder. Is it fine that you run it with your own Claude login or key, or should participants provide credentials or budget for your rerun?
