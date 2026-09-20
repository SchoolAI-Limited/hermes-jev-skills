---
name: jev-model-routing
description: Use to pick the cheapest model that is good enough for a turn — choosing a model, delegating a sub-task to a sub-agent, cutting model spend, or setting up and tuning Jev routing pools.
version: 0.1.0
license: MIT
metadata:
  hermes:
    tags: [jev, typesafe, model-routing, cost]
---

# Model routing with Jev

Jev reads a turn and answers three questions in one ~0.4 s request: how hard is it, what kind of work is it, and would a mistake be costly. Code then walks your pool for that tier and specialty and takes the first model that fits (images, context size). You do not pick models by feel; you ask.

## On Hermes it is automatic

With the `hermes-jev` plugin enabled, each fresh user turn is routed once, before the first model call. Tool-loop follow-ups reuse that decision. Switches, per profile:

```
/jev                    status
/jev routing shadow     decide and log, but do not switch (start here)
/jev routing on         switch models
/jev routing off
/jev notice on          show the decision; in shadow: "WOULD route to …; no model changed."
```

A plugin can swap the model, not the provider connection. On OpenRouter that still means every vendor (DeepSeek, GLM, Kimi, MiniMax, Grok, Qwen, Gemini, GPT). If you run `/model` yourself, your choice wins and Jev stays out of the way.

For `openai-codex`, use **shadow only** and the explicit `codex_shadow_inventory`
in existing `routing.json`, as described in [the private shadow guide](../../docs/private-shadow-trial.md).
Use operator-verified account IDs and ordinary context/capability fields with source
and observation time, not API-vendor aliases or automatic model suggestions. Unknown
required fields keep the current model; text/image support does not establish tool
support. Subscription prices and savings stay unknown. Active Codex routing is not
supported; do not turn it on after the trial or infer that a shadow candidate ran.

## Asking directly (any agent)

Before delegating a task or spawning a sub-agent, ask which model should get it:

```bash
jev route --prompt "<the task, in the person's words>" --current "<provider:model you are on>"
```

Use `model_id` from the reply. `routed: false` means stay where you are; `reason` says why. Relay `notice` if the person likes to see routing.

## The pools

`jev models list` shows every model this machine can call (the models.dev catalog, filtered to providers you hold a key or login for) with price, context and abilities. Pools live in `~/.hermes/jev/routing.json` (or `~/.config/jev/routing.json`):

```json
{"tiers": {"simple": {"general": ["openrouter:deepseek/deepseek-v4.1-flash"], "coding": ["..."]},
           "medium": {"general": ["..."], "coding": ["..."], "research": ["..."], "writing": ["..."], "vision": ["..."]},
           "hard":   {"general": ["..."], "coding": ["..."]}},
 "exclude": ["*:free"], "private_profiles": ["billing"], "mode": "redacted-text"}
```

- `jev models suggest --write` creates a first draft from price bands. Then edit: order matters, first fit wins.
- Specialties are `general`, `coding`, `writing`, `research`, `vision`. A missing specialty falls back to `general`. A pool never falls down a tier, only up.
- When the person names a model they like for something, put it first in that pool. Do not invent model ids: copy them from `jev models list --search <name>`.

## Guarantees you can rely on

- Hard is earned: it needs real probability mass on "substantial" or "expert" (0.6 by default), read from the per-level spread Jev returns, never from an averaged score.
- Unsure is not hard. An unsure answer about a harmless turn keeps the current model; about a risky turn it picks medium.
- Risk words (production, delete, migration, security, payment, legal…) set a floor of medium, however short the prompt. They do not buy the hard tier on their own.
- Jev judges the ask: a long turn is read as its opening plus, mostly, its end (`ask_chars`). Boilerplate in the middle is not what gets scored.
- Template turns are not routed: anything starting with a `skip_prefixes` entry (`[kanban]`, `[SESSION HANDOFF`…) or from a `skip_session_prefixes` session (`cron`) keeps the model its profile or job was configured with.
- Large context (over ~32k tokens): never switches to a cheaper model, because rebuilding the prompt cache costs more than it saves.
- Turns that look like they contain secrets, and any profile listed in `private_profiles`, send Jev only coarse features (length, code present, risk words), never text.
- Jev down, slow (2.5 s budget) or malformed: current model, no delay beyond the budget.

## Tuning

For a private, scoped trial, use [the private shadow guide](../../docs/private-shadow-trial.md); do not enable routing to obtain notices.

Decisions include local `session_id` and `turn_id` (not sent to Jev), cache hits, skipped private skill requests and route failures. They are logged without prompt or response text to `<hermes home>/logs/jev-decisions.jsonl`. Run in `shadow` for a day, read which tier real turns land in, then move models between pools. Change thresholds from your own traces, never from a hunch.
