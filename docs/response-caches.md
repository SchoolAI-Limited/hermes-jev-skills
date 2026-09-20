# Response caches, and what we built instead

We were asked whether [Computer-Use Cache](https://github.com/rohanarun/computer-use-cache)
(MIT) belongs in this repo. It does not, and the reasons are worth keeping, because the same
question will come back about the next cache proxy.

## What it is

An OpenAI-compatible proxy. It hashes a chat-completions request, and when the same request
arrives again it returns the stored response without calling the provider. Exact match only.
An optional mode asks Jev whether a stored response for a *similar* request can be reused; we
have not evaluated that judge, so nothing here is a claim about it either way.

## Why it does little for a Jev-driven loop

A response cache saves a model call. In our loops that call is already the cheap part.

- A Jev decision takes about 0.47 s and costs about $0.00006. One hop of the desktop loop is
  about 4.2 s, and about 2.6 s of that is the driver confirming that the click landed.
- In one measured agent run the runner took 7.9 s of a 36.4 s wall clock. The rest was the
  agent's own turns around it. No cache reaches those: they are streamed, they carry a
  transcript that grows every turn, and so no two are ever byte-identical.

## Why it does nothing for a Hermes lane today

- Hermes streams its main loop, and the proxy never stores a streamed miss. It forwards the
  stream and writes nothing, so there is never an entry to hit.
- A Hermes plugin can swap the *model* for a turn. It cannot swap the *provider connection*,
  and a proxy is a provider connection. Putting one in front of a lane is a change to that
  lane's provider configuration, not something a skill or plugin from this repo can do.

## If you run the proxy anyway

Its defaults are for a demo: all interfaces, any origin, open admin endpoints.

- Leave `UPSTREAM_API_KEY` unset on the proxy, so each client's own bearer token is forwarded.
  Set, it becomes a key that anything able to reach the port can spend.
- Set `CORS_ALLOW_ORIGIN` to one origin (the default is `*`), set `CACHE_ADMIN_TOKEN` (without
  it the stats and clear endpoints are open), and bind it to `127.0.0.1`.
- Pin a commit. It sits on the path of every request and every bearer token.
- Never put it behind `TEXT_MODEL_BASE_URL` for a logged-in site. Requests to that endpoint
  carry text taken from the page, and the proxy writes request bodies to disk in plain text.
  Its secret screen only recognises `name = value` assignments, so a bare token, a cookie or
  a customer's message goes straight into the database.
- Pointing `TEXT_MODEL_BASE_URL` at a local proxy silently drops our reasoning-off flag:
  `jevkit/plan.py` sends it only when the host is `openrouter.ai`. Plans get slower, and a
  reasoning model can spend the token ceiling before any JSON appears.

## What we built instead: the plan cache

The one call in this repo whose answer is a function of its input is `jev plan` / `--plan`:
one text-model call, about 1 s and about $0.0006, repeated for every repeated spoken command.
`jevkit/memo.py` is a small exact-match store and `jevkit/plan.py` uses it. Two ideas are
borrowed from Computer-Use Cache and credited in the code: the key names the endpoint the
answer came from, and any failure is a miss. No code is.

**What is keyed.** The command exactly as given (spacing included, because spacing inside
dictated text changes what is typed and what the never-send filter sees); the front app; the
running apps *the command names* (the full list is z-ordered and changes every run, so it is
left out; the model still gets it on a miss); the model; the endpoint's hostname; and a hash of the prompt,
the step schema, the vocabulary, the step limit and the never-send rule version. Edit the
prompt and every old entry stops matching. There is no fingerprint of the API key.

**What is never stored.** A command `privacy.is_sensitive` flags returns before a key is even
computed. A plan with a step whose target or text looks sensitive is returned and not kept. A
fallback is never kept. Entries hold validated steps only: no prompt, no reply, no key. They
live in `$XDG_CACHE_HOME/jev/memo/plan.json` (default `~/.cache`), mode 0600 in a 0700
directory, at most 256 entries, for 7 days: an older entry is never served, and it leaves
the file the next time a plan is stored. Deleting the file is always safe.

What *is* stored, in `shadow` as well as `on`, includes the words a command dictates: the text
of a `type_text` step, in plain text. "Looks sensitive" means looks like a secret, not looks
private. If dictated text must not sit on disk for a week, set `JEV_MEMO=off`.

**What is still checked.** The cache is a file, so it is not trusted. On every read each step
goes back through the same validation as a model's reply, then through the never-send filter
under today's rules; a hand-edited Send step is dropped like any other. And a cached plan only
replaces the *planning* call. Every step is still observed, chosen, executed and verified
exactly as before. A run that fails a step or ends unverified forgets its plan.

**Modes.** `JEV_MEMO=off|shadow|on`, and `shadow` is the default (see
[turning a Jev feature on](turning-a-jev-feature-on.md)). In `shadow` the model is always
called and nothing is reused; the result's `cache` field records `miss` (no entry yet),
`shadow_agree` (the stored plan equals the fresh one) or `shadow_differ` (it does not, and is
overwritten). `on` reports `hit` or `miss`; `off` reads and writes nothing.

`jev memo stats` shows how many plans are stored and how large the file is, never a key or a plan. `jev memo clear` empties it.

**Before turning it on,** read a batch of `cache` values from your own `--json` results
(`plan.cache`). Mostly `miss` means commands do not repeat and the cache buys nothing. A
`shadow_differ` means the model gave two different plans for the same command in the same
context: read both, because with `on` you would have got the older one. Turn it on when the
repeats agree. A cache can only ever be as right as the first answer it kept.
