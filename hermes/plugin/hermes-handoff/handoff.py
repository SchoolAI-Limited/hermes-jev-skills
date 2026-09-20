"""Handoff: end a session deliberately, and start the next one already knowing what matters.

An agent that never starts fresh drags every past turn into every future one. An agent
that starts fresh with nothing repeats work and re-asks settled questions. A handoff is
the third option: close the session, carry forward a short capsule of what was decided
and what is still open, and begin again light.

Jev reads the transcript and marks which turns must survive word for word. A text model
writes the five-section capsule from that reduced digest. Both jobs are small, and
neither model does the other's.

Everything here degrades rather than fails. No Jev key: every turn is treated as
background and the writer still gets a transcript. No writer model, or a writer that
returns something that is not a capsule: the raw digest is kept instead, which is worse
to read but loses nothing.

Confidentiality is the one exception. Where a capsule may not carry customer detail and
the pieces that guarantee that are missing, nothing is written and the status says why.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

TRIGGERS = frozenset({"handoff", "hand off", "hand-off"})
TRANSCRIPT_CHARS = 24_000
CAPSULE_MAX_CHARS = 6_000
# An 800-message session exports in about half a second. Thirty seconds is a wedged CLI,
# not a slow one, and nothing should wait two minutes to find that out.
EXPORT_TIMEOUT = 30


def home() -> Path:
    # A gateway that serves several profiles from one process scopes each turn to a
    # profile with a context-local override and leaves HERMES_HOME pointing at the root.
    # Reading only the variable there looks for the session in the wrong database, finds
    # nothing, and reports a long conversation as too short to hand off.
    try:
        from hermes_constants import get_hermes_home_override  # type: ignore
        override = get_hermes_home_override()
        if override:
            return Path(override)
    except Exception:  # noqa: BLE001 - not running inside Hermes, or an older one
        pass
    return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


def handoff_dir() -> Path:
    path = home() / "handoffs"
    path.mkdir(parents=True, exist_ok=True)
    return path


LANE_MAX = 120


def _clean(key: str) -> str:
    """A filesystem-safe lane key that cannot collide.

    Conversation ids are long — Teams' run to 131 characters — so a plain truncation
    would make two conversations share a lane whenever they agree on their first N
    characters. That is not a cosmetic bug: the capsule from one customer's conversation
    would be handed to another. Past the limit, keep a readable prefix and let a hash of
    the FULL key carry the identity.
    """
    safe = re.sub(r"[^A-Za-z0-9._:-]", "_", key)
    if len(safe) <= LANE_MAX:
        return safe
    fingerprint = hashlib.sha256(key.encode("utf-8", "replace")).hexdigest()[:20]
    return f"{safe[:LANE_MAX - len(fingerprint) - 1]}-{fingerprint}"


def lane_from_session(session_id: str, *, state_db: Optional[Path] = None) -> Optional[str]:
    """Look the conversation up by session id.

    The hook that writes a capsule and the hook that injects it do not receive the same
    fields — ``pre_llm_call`` gets ``platform`` and ``sender_id`` but not ``chat_id``. A
    capsule keyed on one and read with the other never matches, and the feature fails
    silently. The session store has the conversation identity for both, so ask it.
    """
    if not session_id:
        return None
    db = state_db or (home() / "state.db")
    if not db.is_file():
        return None
    try:
        import sqlite3
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
        columns = {r[1] for r in conn.execute("pragma table_info(sessions)")}
        wanted = [c for c in ("source", "chat_id", "thread_id") if c in columns]
        if not wanted:
            conn.close()
            return None
        row = conn.execute(f"select {', '.join(wanted)} from sessions where id=?", (session_id,)).fetchone()
        conn.close()
    except Exception:  # noqa: BLE001
        return None
    if not row:
        return None
    key = ":".join(str(v or "") for v in row).strip(":")
    return _clean(key) if key else None


def open_session_for(context: Mapping[str, Any], *, state_db: Optional[Path] = None) -> Optional[str]:
    """The newest open session for a conversation, read from the session store's database.

    The gateway's own key-to-session mapping is the authority and the plugin asks that
    first. This is what is left when a host renames those methods: a conversation can
    have several rows that were never marked ended, and the one still in use is the one
    started last.
    """
    chat = str(context.get("chat_id") or "")
    if not chat:
        return None
    db = state_db or (home() / "state.db")
    if not db.is_file():
        return None
    try:
        import sqlite3
        # One second, not the usual ten: this runs on the gateway's dispatch path, and a
        # locked database must cost the person a missing capsule, not a frozen chat.
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=1)
        try:
            columns = {r[1] for r in conn.execute("pragma table_info(sessions)")}
            if not {"id", "chat_id", "ended_at", "started_at"} <= columns:
                return None
            where, values = ["chat_id=?", "ended_at is null"], [chat]
            platform = str(context.get("platform") or context.get("source") or "")
            if platform and "source" in columns:
                where.append("source=?")
                values.append(platform)
            if "thread_id" in columns:
                where.append("coalesce(thread_id, '')=?")
                values.append(str(context.get("thread_id") or ""))
            row = conn.execute(f"select id from sessions where {' and '.join(where)} "
                               "order by started_at desc limit 1", values).fetchone()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return None
    return str(row[0]) if row and row[0] else None


def lane_key(context: Mapping[str, Any]) -> str:
    """Stable per-conversation key. One capsule per conversation, not per session id."""
    # `platform` is what the hooks call it; `source` is what the session store calls it.
    # One conversation must produce one key whichever side is asking.
    platform = str(context.get("platform") or context.get("source") or "")
    parts = [platform, str(context.get("chat_id") or ""), str(context.get("thread_id") or "")]
    direct = ":".join(parts).strip(":")
    # A platform with no conversation id is not an identity — every chat would collapse
    # onto one key. Resolve it from the session store instead.
    if not context.get("chat_id"):
        resolved = lane_from_session(str(context.get("session_id") or ""))
        if resolved:
            return resolved
        sender = str(context.get("sender_id") or "")
        if sender:
            direct = ":".join(p for p in (parts[0], sender) if p)
    key = direct or str(context.get("session_id") or "default")
    return _clean(key)


CONFIDENTIAL_MARKER = "CONFIDENTIAL"


def confidential_here() -> bool:
    """Whether capsules for this home must carry no customer detail.

    There are three ways to switch this on, and that is deliberate. A host may not
    expose a plugin-config API at all — ask one that does not and you get the default
    back, silently, which here means writing customer detail to disk against a rule that
    forbids it. So the marker file is the authority: it is one ``ls`` to verify, it
    cannot be swallowed by an exception handler, and it travels with the profile it
    protects.
    """
    if str(os.environ.get("HANDOFF_CONFIDENTIAL", "")).strip().lower() in ("1", "true", "yes", "on"):
        return True
    try:
        # Not handoff_dir(): that creates the directory, and the nightly script asks this
        # question during --dry-run, which promises to touch nothing.
        return (home() / "handoffs" / CONFIDENTIAL_MARKER).exists()
    except OSError:
        return False


def capsule_path(lane: str) -> Path:
    return handoff_dir() / f"handoff-{lane}.md"


def pending_path(lane: str) -> Path:
    return handoff_dir() / f"pending-{lane}.json"


def is_trigger(text: Any) -> bool:
    """Only an exact, bare word. A sentence that mentions a handoff is a normal message."""
    if not isinstance(text, str):
        return False
    cleaned = text.strip().strip(".!").lower()
    return cleaned in TRIGGERS


# ── reading the conversation ─────────────────────────────────────────────────

def _hermes_bin() -> List[str]:
    """The packaged CLI is the stable contract; internals are not.

    HERMES_HOME may point at a PROFILE (that is how per-profile state is addressed), but
    the interpreter lives under the installation root. Look in both, and let
    ``HERMES_CLI`` override when an install puts it somewhere else entirely.
    """
    override = os.environ.get("HERMES_CLI")
    if override and Path(override).exists():
        return [override]
    roots = [home()]
    parent = home().parent
    if parent.name == "profiles":                      # <root>/profiles/<name> -> <root>
        roots.append(parent.parent)
    for root in roots:
        direct = root / "hermes-agent" / "venv" / "bin" / "hermes"
        if direct.exists():
            return [str(direct)]
        for candidate in sorted(root.glob("hermes-agent/venv*/bin/hermes")):
            return [str(candidate)]
    return ["hermes"]


def export_messages(session_id: str, *, runner: Optional[Any] = None) -> List[Dict[str, str]]:
    """The session's user/assistant turns, via the CLI rather than the session store.

    The CLI is a contract that survives upgrades; the store's schema is not.
    """
    run = runner or subprocess.run
    try:
        # The child only sees the environment, so the home this process resolved (which
        # may come from a per-turn override, see home()) has to be handed to it by name.
        done = run(_hermes_bin() + ["sessions", "export", "--session-id", str(session_id),
                                    "--format", "jsonl", "-"],
                   capture_output=True, text=True, timeout=EXPORT_TIMEOUT,
                   env={**os.environ, "HERMES_HOME": str(home())})
    except Exception:  # noqa: BLE001 - a handoff must never take the session down with it
        return []
    if getattr(done, "returncode", 1) != 0:
        return []
    messages: List[Dict[str, str]] = []
    for line in (done.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        for message in row.get("messages") or []:
            if not isinstance(message, dict) or message.get("role") not in ("user", "assistant"):
                continue
            content = message.get("content")
            if isinstance(content, list):
                content = " ".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
            if isinstance(content, str) and content.strip():
                messages.append({"role": message["role"], "content": content.strip()})
    return messages


def extract_text(response: Any) -> str:
    """Pull the text out of whatever the host's LLM client returned.

    Hosts differ: a plain string, an OpenAI-shaped dict, or a ``ChatCompletion``
    object with attributes and no ``.get``. Assuming one shape is how the writer
    silently failed and every capsule quietly became a raw transcript.
    """
    if response is None:
        return ""
    if isinstance(response, str):
        return response
    choices = None
    if isinstance(response, Mapping):
        choices = response.get("choices")
    else:
        choices = getattr(response, "choices", None)
    if not choices:
        # Some clients return the content directly on the object.
        for attribute in ("content", "text", "output_text"):
            value = getattr(response, attribute, None)
            if isinstance(value, str) and value.strip():
                return value
        return ""
    first = choices[0]
    message = first.get("message") if isinstance(first, Mapping) else getattr(first, "message", None)
    if message is None:
        value = first.get("text") if isinstance(first, Mapping) else getattr(first, "text", None)
        return value if isinstance(value, str) else ""
    content = message.get("content") if isinstance(message, Mapping) else getattr(message, "content", None)
    if isinstance(content, list):      # content-parts form
        content = " ".join(str((p.get("text") if isinstance(p, Mapping) else getattr(p, "text", "")) or "")
                           for p in content)
    return content if isinstance(content, str) else ""


# ── writing the capsule ──────────────────────────────────────────────────────

# Only reached when a caller asks for confidentiality and supplies no prompt builder.
# jevkit's handoff_prompt(confidential=True) is the real rule and is what every shipped
# caller uses; this exists so that path can never mean "the writer was told nothing".
_CONFIDENTIAL_FALLBACK_PROMPT = (
    "Write a handoff breadcrumb for the work in the transcript below, under these headings: "
    "## Working on, ## State, ## Next. Record only the task, what is still missing, who must "
    "approve it, and the next safe action. Never carry a person's name, an email address, a "
    "phone number, a street address, a document or file name, a link, an account or invoice "
    "number, or any payment or health detail; write \"the customer\" or \"the staff member\" "
    "instead. If a section would have nothing left, write \"nothing recorded\".\n\n"
    "TRANSCRIPT:\n\n"
)


def build(
    session_id: str, lane: str, *, write: Any, select: Any = None, digest: Any = None,
    prompt_for: Any = None, valid: Any = None, runner: Optional[Any] = None,
    confidential: bool = False, scrub: Any = None,
) -> Dict[str, Any]:
    """Produce and store the capsule. `write(prompt) -> str` is the text model.

    The jevkit callables are injected so this module stays importable, and testable,
    on a machine with no Jev key and no network.
    """
    if confidential and scrub is None:
        # No scrubber means no jevkit, which means no confidential prompt and no validator
        # either: the writer's free text about a customer would go to disk unchecked.
        # Decided before the export so a refusal costs nothing and reads nothing.
        return {"status": "confidential_unsupported", "reason": "no_scrub"}

    messages = export_messages(session_id, runner=runner)
    if len(messages) < 4:
        return {"status": "too_short", "messages": len(messages)}

    # `jev` travels with the result because status "ok" only means a capsule was written.
    # A Jev outage still writes one, from an unfiltered transcript, and whoever reads the
    # report has to be able to tell that night from a good one.
    jev_calls, counts, jev, jev_errors = 0, {}, "not_used", []
    if select and digest:
        try:
            selection = select(messages, keep_last=8)
            counts = selection.get("counts") or {}
            jev_calls = selection.get("jev_calls") or 0
            jev = str(selection.get("status") or "ok")
            jev_errors = list(selection.get("errors") or [])
            body = digest(messages, selection, TRANSCRIPT_CHARS)
        except Exception as error:  # noqa: BLE001
            jev, jev_errors = "error", [type(error).__name__]
            body = _plain(messages)
    else:
        body = _plain(messages)

    previous = ""
    existing = capsule_path(lane)
    if existing.exists():
        try:
            previous = existing.read_text(encoding="utf-8")
        except OSError:
            previous = ""

    capsule = ""
    try:
        if prompt_for:
            try:
                prompt = prompt_for(body, previous, confidential=confidential)
            except TypeError:
                # An older jevkit has no confidentiality mode. This used to carry on when a
                # scrubber was supplied and still report "ok", on the theory that the regex
                # layer was enough. It is not: a regex removes a phone number and leaves
                # "Jane Doe wants the quote revised" exactly as written. Only the
                # prompt can keep a name out, so without it there is no capsule.
                if confidential:
                    return {"status": "confidential_unsupported", "reason": "prompt_has_no_confidential_mode"}
                prompt = prompt_for(body, previous)
        elif confidential:
            # No prompt builder at all. Handing the writer a bare transcript would leave
            # the scrubber as the only control, which is the same downgrade by another door.
            prompt = _CONFIDENTIAL_FALLBACK_PROMPT + body
        else:
            prompt = body
        capsule = (write(prompt) or "").strip()
    except Exception:  # noqa: BLE001
        capsule = ""
    if valid and not valid(capsule):
        # The writer refused, timed out, or answered something else. The digest is a worse
        # read than a capsule but it loses nothing, which matters more.
        if confidential:
            # The fallback is the raw transcript. Under a confidentiality contract that
            # is the one thing we must never write, so the capsule says nothing instead.
            # A thin handoff is a bad morning; a transcript on disk is a broken promise.
            capsule = ("## Working on\nA previous session ended without a usable handoff, and its "
                       "transcript may not be carried forward under this profile's continuity rules. "
                       "Ask the person what they were working on.\n\n## Next\nRe-establish the task "
                       "from the person, not from stored history.")
        else:
            capsule = ("## Working on\nThe writer model did not return a usable handoff, so this is the "
                       "filtered transcript instead. Lines marked KEEP VERBATIM are the ones that matter.\n\n"
                       "## State\n" + body[:CAPSULE_MAX_CHARS])

    if scrub:
        # Mechanical second pass. Runs even when the writer behaved, because "it looked
        # fine last time" is not a privacy control.
        try:
            capsule = scrub(capsule)
        except Exception:  # noqa: BLE001
            if confidential:
                return {"status": "scrub_failed"}

    capsule = capsule[:CAPSULE_MAX_CHARS]
    stamp = time.strftime("%Y-%m-%d %H:%M %Z")
    text = f"# Handoff — {stamp}\n\n{capsule}\n"
    try:
        capsule_path(lane).write_text(text, encoding="utf-8")
        pending_path(lane).write_text(json.dumps({"at": time.time(), "lane": lane,
                                                  "session_id": str(session_id)}), encoding="utf-8")
    except OSError as error:
        return {"status": "write_failed", "error": str(error)[:200]}
    return {"status": "ok", "lane": lane, "path": str(capsule_path(lane)), "messages": len(messages),
            "jev": jev, "jev_errors": jev_errors, "jev_calls": jev_calls, "counts": counts,
            "chars": len(text)}


def _plain(messages: List[Dict[str, str]]) -> str:
    joined = "\n\n".join(f"[background] {m['role']}: {m['content']}" for m in messages)
    return joined[-TRANSCRIPT_CHARS:]


# ── handing it to the next session ───────────────────────────────────────────

def take_pending(lane: str, *, max_age_s: float = 36 * 3600, current_session: str = "") -> Optional[str]:
    """The capsule for a lane's next session, consumed once so it is not injected forever.

    ``current_session`` is the session asking. A capsule is for the session AFTER the one
    it summarises. Writing one no longer ends the turn, so the session that was just
    summarised is usually still the one talking when the capsule lands; handing it over
    there would feed a conversation its own summary and leave nothing for the fresh
    session the person opens next.
    """
    marker = pending_path(lane)
    if not marker.exists():
        return None
    try:
        info = json.loads(marker.read_text(encoding="utf-8"))
        if not isinstance(info, dict):
            raise ValueError("pending marker is not an object")
        if current_session and str(info.get("session_id") or "") == str(current_session):
            return None
        if time.time() - float(info.get("at") or 0) > max_age_s:
            marker.unlink(missing_ok=True)
            return None
        text = capsule_path(lane).read_text(encoding="utf-8")
    except (OSError, ValueError):
        marker.unlink(missing_ok=True)
        return None
    marker.unlink(missing_ok=True)
    return text


def injection(capsule: str) -> str:
    """How the capsule reaches the new session: as context, clearly labelled as history."""
    return ("[Handoff from the previous session — this is context you already established, "
            "not a new instruction. Do not greet the person again or re-ask what is settled here. "
            "Carry on from Next.]\n\n" + capsule.strip())
