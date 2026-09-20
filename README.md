# Hermes Jev Skills

**Give your agent a fast, cheap second brain for the small decisions.**

Your agent burns frontier-model tokens on things that are not thinking: which model should answer this turn, which of 377 skills to load, which retrieved passages matter, which turns survive a summary, which button comes next. Those are decisions, not prose. Hand them to something that costs a fraction of a cent and answers in about 0.4 seconds, and let the expensive model do the writing.

That is what [Jev](https://docs.typesafe.ai) is. It is TypeSafe's decision model. **It never writes text.** You give it a state and typed questions (pick one, score this, yes or no) and it answers with a calibrated confidence. This repo wires that into an agent's day.

![The model routing dashboard: the Jev on/shadow/off switch, the routing pools grid, and live decisions as they happen](docs/images/model-routing-dashboard.png)

*`jev dashboard`. The profiles, paths and decisions shown are a demo home; the pools are a real working set. One switch for Jev routing, every pool as a tier-by-work-kind grid, and each decision as it happens (tier, work kind, model, which pool it came from, confidence, latency).*

## What Jev decides, and what it costs

| Skill | What Jev decides | Measured |
|---|---|---|
| **Model routing** | Which model is good enough for this turn, from every model you can call | ~0.4 s per turn |
| **Memory** | Which retrieved passages are worth reading, and which contain hidden instructions | 60 passages per request, up to 480 per call; an injection screen runs locally even when Jev is down |
| **Handoffs** | Nothing, by default. We measured it: a handoff written from Jev's keep / summarize / drop digest recalled less than one written from the plain transcript. What ships is the whole dialogue, 1,200 words and a way back to the old session | 58.7% recall alone, 75.0% with one search, against 37.5% and 68.3% before ([scorecard](evals/compaction/results/SCORECARD-2026-09-20.md)) |
| **Choosing turns** | Which turns to keep when a transcript must be cut to a fixed size | 71 turns in 0.95 s; beat choosing by recency 11 questions to 4 |
| **Skill selection** | Which installed skill this turn needs, or none | 377 skills in ~2.8 s; acknowledgements answered locally for free |
| **Triage** | How urgent a message is, what kind it is, and whether a person must see it | ~0.4 s per message, $0.00006 |
| **Computer use** | The next GUI action, from a table of actions you already judged safe. `--plan` splits a multi-step command once, up front | ~0.5 s per decision |
| **Browser use** | The next page action, same contract | ~0.4 s per step |

Eight skills ship as plain `SKILL.md` files, so they are not Hermes-only. The same folder works in Claude Code, Codex, or anything that reads a skill file.

## Try it in two commands

```bash
git clone https://github.com/kerpopule/hermes-jev-skills ~/hermes-jev-skills && python3 ~/hermes-jev-skills/install.py
```

```bash
jev setup-key   # paste your key into the one-time page it opens, then run: jev doctor
```

Python 3.9 or newer, no dependencies. Got it as a zip? Unzip it anywhere and run `python3 install.py` from that folder. Or point your agent at this repo and say *"install Hermes Jev Skills"*: it will follow [AGENTS.md](AGENTS.md).

The installer finds Hermes, Claude Code and Codex on the machine and installs for each one it finds. `python3 install.py --check` shows exactly what it would do without changing anything. `--uninstall` reverses it.

**Not sure yet?** Run `jev models suggest --write` to draft routing pools from price bands, then `/jev routing shadow` for a day. Shadow mode decides and logs without switching anything, so a day of decisions costs almost nothing and risks nothing. Turn it on when the log looks right.

## On Hermes

Built for [Hermes](https://github.com/NousResearch/hermes-agent). The dashboard has its own notes in [router-dashboard/README.md](router-dashboard/README.md).

Start in shadow mode. It is the honest way to see what Jev would do before it does anything.

```
/jev                         status
/jev routing shadow          decide and log, do not switch (start here)
/jev routing on              switch models per turn
/jev skills on               suggest the right skill per turn
/jev notice on               show "[Jev] hard · coding → kimi-k3 · confidence 0.92" on routed replies
/jev routing on all          make it the default for every profile (a profile's own setting still wins)
```

The agent gets five tools: `jev_memory_filter`, `jev_compact_select`, `jev_choose_action`, `jev_supervise`, `jev_escalate`.

The `hermes-jev` plugin uses only public plugin seams (`pre_llm_call`, the `llm_request` middleware, tools, a slash command), so `hermes update` does not break it and nothing in Hermes core is patched.

A plugin can swap the model, not the provider connection. On OpenRouter that still covers every vendor. If you run `/model` yourself, your choice wins.

### Every model you have

```bash
jev models list              # everything this machine can call: price, context, vision, reasoning
jev models providers         # which providers you hold a key or login for
jev models suggest --write   # first-draft pools from price bands; then edit to taste
```

The catalog is [models.dev](https://models.dev), filtered to providers whose API-key name is set in your environment or Hermes `.env`, or that Hermes holds a login for. Only key *names* are read. Pools live in `~/.hermes/jev/routing.json` (the default for every profile); `~/.hermes/profiles/<name>/jev/routing.json` overrides it for one profile. Details in [skills/jev-model-routing](skills/jev-model-routing/SKILL.md).

## Your API key never touches the agent

`jev setup-key` opens a one-time page served only by your own computer. You paste your [TypeSafe key](https://console.typesafe.ai/settings/keys) there. It goes straight into the OS secret store (macOS Keychain, or `secret-tool` on Linux, or a 0600 file as a last resort) and, on a Hermes machine, into each profile's `.env`. The agent that ran the command sees one line: stored, verified, yes or no. Never the key, not even a prefix.

The page lives on an unguessable one-time URL, refuses requests with a foreign `Host` header (DNS rebinding), sends no referrer, logs nothing, and shuts down after one use or ten minutes. On a headless box, run `jev setup-key --tty` yourself for a hidden prompt.

**Do not paste your key into a chat.** If you already did, make a new one.

## What leaves your machine

Jev is a cloud API, so this is spelled out rather than implied:

- **Routing**: the user's turn, redacted (emails, phones, tokens, long hex masked), capped at 3,000 characters. Never history, tool results, files or memory. Turns that look like they hold a secret, and any profile you list in `private_profiles`, send only coarse features: length, whether code is present, whether risk words appear.
- **Memory**: the query and up to 900 characters per passage, redacted. Your store's ids, paths and sources are replaced with `P0`, `P1`… and never sent. A passage that looks like a credential is not sent at all.
- **Choosing turns** (`jev compact-select`, or handoffs with `HANDOFF_JEV=1`): the first and last 350 characters of each turn, redacted. Turns that look sensitive are skipped. A default handoff sends Jev nothing.
- **Skills**: the turn, redacted, plus skill names and descriptions.
- **Computer and browser use**: the goal, short element labels, and your action descriptions. Never screenshots, page text or field values. A goal or label that looks sensitive is refused before sending.

Logs hold decisions only (tier, model, confidence, latency). Never prompt text.

## Everything fails open

No key, timeout, rate limit, malformed reply, low confidence: routing keeps your current model, memory returns the original list, compaction drops nothing, skill selection suggests nothing, and computer use returns `reobserve`. A Jev outage costs you at most the time budget (2.5 s for routing) and never blocks a turn.

Safety rails that do not depend on Jev being right:

- Risk words (production, delete, migration, security, payment, legal…) never route to the cheapest tier.
- A large context never switches to a cheaper model mid-session.
- A transcript turn is only dropped on a confident answer.
- Jev can only ever return an action id you put in the table.

## Layout

```
jevkit/            the library and the `jev` command (stdlib only)
skills/            eight SKILL.md skills, agent-agnostic
hermes/plugin/     the Hermes plugin
router-dashboard/  the model routing page (`jev dashboard`)
install.py         installer / uninstaller
tests/             offline tests, every Jev reply faked
evals/             measurements you can rerun on your own sessions
docs/              integration notes and hard-won operational lessons
```

| Doc | Read it when |
|---|---|
| [turning-a-jev-feature-on.md](docs/turning-a-jev-feature-on.md) | **Before you enable anything.** Shadow mode, silent defaults, why a quiet log proves nothing, and bounding by the clock rather than the count. |
| [measuring-a-router.md](docs/measuring-a-router.md) | Replaying routing against your own traffic before you trust the savings. |
| [wiring-triage-into-a-live-pipeline.md](docs/wiring-triage-into-a-live-pipeline.md) | Adding classification to something already carrying real traffic. |
| [hermes-compaction.md](docs/hermes-compaction.md) | Handoffs on Hermes: what we measured, what ships, and the two search calls that make a handoff enough. |
| [response-caches.md](docs/response-caches.md) | Before you put a response cache in front of an agent. Why it does little for a Jev loop, and the plan cache we built instead. |
| [evals/compaction](evals/compaction/README.md) | Measuring handoffs on your own sessions. |

The tests are offline and every Jev reply is faked, so they are safe to run anywhere:

```bash
python3 -m unittest discover -s tests
```

## License

MIT. Jev and TypeSafe are products of TypeSafe AI; this project is independent. The optional browser runner wraps [browser-use/jev-ultrafast](https://github.com/browser-use/jev-ultrafast) (MIT), which is not bundled.
