# Private shadow trial (operator-reviewed, not an activation script)

This trial observes routing decisions without changing the main model request. It is
not a zero-egress mode: routing sends coarse features to Jev (length bucket, question
count, code/step/risk flags and context bucket), not private turn text. The routed
model/provider IDs stay local. The normal Hermes model request is outside this boundary.

**Private skill suggestions are deliberately unavailable.** `skills=on` together with
`private_profiles` records a local `private_profile` skip. It does not transmit the
turn, skill names, descriptions or a semantic surrogate. Do not count these skips as
successful classifier decisions. Normal local skill use remains available.

## Explicit trial settings

After independent review and backup, prepare the target profile's `jev/state.json`
with these settings before loading the plugin. This file takes precedence over plugin
config and shared switches; merge deliberately with existing state rather than blindly
replacing it:

```json
{
  "routing": "shadow",
  "skills": "on",
  "notice": "on",
  "memory": "off",
  "compaction": "off",
  "actions": "off",
  "supervision": "off",
  "escalation": "off"
}
```

In the target profile's `jev/routing.json`, merge these fields with the already-reviewed
model pools. Replace `private-test` with the actual profile directory name (or `default`
for a root home). Do not invent pool IDs or fetch a new catalog as part of this handoff.

```json
{
  "mode": "features",
  "private_profiles": ["private-test"],
  "escalation": {"enabled": false, "rungs": []}
}
```

Check effective configuration precedence: `JEV_ROUTING_CONFIG`, if present, replaces
normal routing-config discovery; it must not silently bypass these settings. Verify
configuration without reading credentials. No pools means a logged keep decision and
no routing classifier request. No key means fail-open; this guide grants no permission
to connect a key, spend credits, restart a gateway or make live test requests.

### Gates and compatibility

- `memory`, `compaction`, `actions` (both GUI and browser) and `supervision` default to
  **on** for existing installations. Trial state explicitly sets each **off**.
- `escalation` defaults to the existing `routing.json` `escalation.enabled` value.
  Explicit **off** blocks the handler and routing's ladder selection, even if inherited
  routing configuration enables escalation. Cache keys include effective configuration.
- Registered tool handlers check their gate at invocation, including explicit calls with
  malformed/empty arguments. Disabled tools return `status=disabled`, never call Jev or
  the escalation ladder, and do not process their input.
- Startup prompt rules omit instructions to use disabled tools. Change trial settings
  before a fresh session; an existing session's prompt is not rewritten mid-conversation.
- These are **plugin** gates, not a sandbox or global CLI policy. Direct library calls,
  `bin/jev` commands and separately installed browser/computer runners are outside them.
  This install excludes those skills/runners and handoff/nightly integration. Do not use
  direct commands to bypass an OFF gate.

## Exact scoped install plan

The operator supplies `TARGET_HERMES_HOME`, an existing, backed-up Hermes home containing
`config.yaml`. Use a frozen reviewed checkout, not a moving `git pull`. First preview:

```bash
python3 install.py --hermes-only --hermes-home "$TARGET_HERMES_HOME" \
  --plugins hermes-jev --skills none --scripts none --enable all --check
```

Review the report: exactly one home, only `hermes-jev`, no skills/scripts, no CLI link,
no other agents and zero discovered child profiles. After separate installation approval,
run the identical command without `--check`:

```bash
python3 install.py --hermes-only --hermes-home "$TARGET_HERMES_HOME" \
  --plugins hermes-jev --skills none --scripts none --enable all
```

`--enable all` means the **single target** in this scope, not the fleet. No new skill is
needed for the routing or skill hooks. Existing skills and unselected plugins/scripts
are not removed or disabled; audit pre-existing installations separately. Symlinked
write targets are refused rather than following a shared fleet link. Default installation
without `--hermes-only` retains its historical multi-agent/fleet behavior.

After an authorized install, verify the target plugin/version and enabled list, effective
privacy/gates and untouched neighboring homes. Start only an authorized fresh session.
Do not turn routing on to obtain a notice: shadow produces `WOULD route to …; no model
changed.` or `WOULD keep …; no model changed.` The notice changes displayed output only;
the middleware returns no replacement request in shadow.

Scoped removal uses the same selections, with a read-only preview first:

```bash
python3 install.py --hermes-only --hermes-home "$TARGET_HERMES_HOME" \
  --plugins hermes-jev --skills none --scripts none --enable all --uninstall --check
python3 install.py --hermes-only --hermes-home "$TARGET_HERMES_HOME" \
  --plugins hermes-jev --skills none --scripts none --enable all --uninstall
```

This removes the selected installed artifacts and their enabled entries, not decision logs,
state, routing configuration, backups, keys or unrelated skills. Restore pre-trial content
from the operator's backup if replacing an earlier installation. Installation is not an
atomic transaction; inspect any partial failure before starting Hermes.

## Decision evidence and its limits

Local `logs/jev-decisions.jsonl` records routing/skill decisions with `session_id` and
`turn_id`, never turn or response text. Skill records use selected counts, not skill names.
Route cache hits retain `cached=true`; unavailable routing and skill decisions retain
fail-open reasons; private skill skips and disabled explicit tool calls are recorded.
Tool-loop reuse does not emit another route row. Missing host IDs stay empty/null rather
than inventing correlation. IDs are not added to provider request bodies or headers.
Keep IDs opaque and keep these local logs private; they are not telemetry for this repo.

Join route and skill rows by session/turn IDs. Separate skipped, failed, cached and fresh
classifier decisions before calculating rates or latency. Cache-hit rows retain the
original decision latency, not a new request measurement. Feature-only routing is a
weaker signal than semantic classification; these observations do not establish answer
quality, realized savings, or safe thresholds for active routing. Private skill skips
cannot validate the skill classifier. No model switch occurs, so savings remain estimates.

`tests/test_private_shadow.py` exercises synthetic private routing/skills, shadow request
byte equality, explicit handler gates and prompt rules, matching logs, and temporary-home
scoped check/install/uninstall. All transports are fake. Live Hermes hook delivery,
streaming notice presentation and provider billing remain unverified until separately
authorized. Independent review belongs to the operator, not this patch's author.
