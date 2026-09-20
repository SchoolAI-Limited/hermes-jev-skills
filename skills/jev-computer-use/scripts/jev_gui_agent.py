#!/usr/bin/env python3
"""Bounded GUI computer-use loop: cua-driver (MCP) + Jev.

The counterpart to jev_browser_agent.py for desktop surfaces. You own the
planner role; Jev only ever picks one id from a table you built. It never
writes coordinates, selectors or text.

  observe (cua-driver) -> element table -> jev choose -> one action -> observe

Usage:
  python3 jev_gui_agent.py --pid 26955 --window-id 46041 \
    --goal 'Open the Library page in YouTube Music' \
    --expect 'Library' --max-steps 12 --json

Exit codes: 0 verified, 4 unverified, 2 refused to start, 6 abstained.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------- jevkit

def _repo_root() -> Path | None:
    """Find jevkit from wherever this file actually lives.

    The first version only walked its own parent directories, which works inside the
    repo checkout and NOWHERE ELSE. Installed as a skill under ~/.hermes/skills/ — the
    only place an agent ever runs it from — no parent holds jevkit, so every agent got
    "jevkit not importable" on the first call. It passed every test because every test
    ran from the checkout. The installer vendors jevkit inside the Hermes plugin, and
    the `jev` CLI symlink points into a checkout, so both are searched.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "jevkit" / "choose.py").is_file():
            return parent
    homes = [os.environ.get("HERMES_HOME"), str(Path.home() / ".hermes")]
    for home in filter(None, homes):
        root = Path(home)
        # HERMES_HOME is often a PROFILE dir (<root>/profiles/<name>); plugins live at the root.
        bases = [root] + ([root.parent.parent] if root.parent.name == "profiles" else [])
        for base in bases:
            candidate = base / "plugins" / "hermes-jev"
            if (candidate / "jevkit" / "choose.py").is_file():
                return candidate
    cli = shutil.which("jev")
    if cli:
        for parent in Path(cli).resolve().parents:
            if (parent / "jevkit" / "choose.py").is_file():
                return parent
    return None


ROOT = _repo_root()
if ROOT is not None and str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from jevkit.choose import choose as jev_choose
    from jevkit.privacy import is_sensitive
except Exception:  # noqa: BLE001
    jev_choose = None

    def is_sensitive(text: str) -> bool:  # type: ignore[misc]
        return False


def _find_driver() -> str:
    """Locate cua-driver without baking anyone's home directory into the repo.

    A hardcoded /Users/<someone>/ path is both wrong on every other machine and blocked
    by scripts/check_release.py, which is the gate that keeps fleet-specific runtime out
    of the public skill. Order: explicit override, PATH, then the usual install sites.
    """
    override = os.environ.get("CUA_DRIVER_BIN")
    if override:
        return override
    found = shutil.which("cua-driver")
    if found:
        return found
    for candidate in (Path.home() / ".local" / "bin" / "cua-driver",
                      Path("/Applications/CuaDriver.app/Contents/MacOS/cua-driver"),
                      Path("/usr/local/bin/cua-driver")):
        if candidate.exists():
            return str(candidate)
    return "cua-driver"      # let the failure name the missing binary


CUA = _find_driver()

INTERACTIVE_ROLES = {
    "AXButton", "AXLink", "AXTextField", "AXCheckBox", "AXRadioButton",
    "AXPopUpButton", "AXComboBox", "AXMenuItem", "AXSearchField",
    "AXTab", "AXSlider", "AXDisclosureTriangle", "AXSegmentedControl",
}

# The chooser contract caps a table at 32 candidates. build_table always appends these,
# so the element budget is whatever is left. Stated here once rather than as a magic 26.
STANDARD_ACTIONS = (
    ("scroll-down", "Scroll the page down to reveal more elements."),
    ("scroll-up", "Scroll the page up."),
    ("wait", "Wait one second for the page to finish changing."),
    ("reobserve", "Take a fresh observation without changing anything."),
    ("done", "The goal is fully achieved and independently verified."),
    ("abstain", "Stop and ask the person for help."),
)
MAX_CANDIDATES = 32
MAX_REGIONS = MAX_CANDIDATES - len(STANDARD_ACTIONS)


# ---------------------------------------------------------------- MCP client


class Driver:
    """Minimal MCP stdio client for cua-driver."""

    def __init__(self, binary: str = CUA) -> None:
        self.proc = subprocess.Popen(
            [binary, "mcp", "--no-daemon-relaunch"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1,
        )
        self._id = 0
        self.call("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "jev-gui-agent", "version": "0.1.0"},
        })
        self._notify("notifications/initialized")

    def _send(self, obj: dict) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(obj) + "\n")
        self.proc.stdin.flush()

    def _notify(self, method: str) -> None:
        self._send({"jsonrpc": "2.0", "method": method})

    def call(self, method: str, params: dict, timeout: float = 90.0) -> dict:
        self._id += 1
        mid = self._id
        self._send({"jsonrpc": "2.0", "id": mid, "method": method, "params": params})
        assert self.proc.stdout is not None
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = self.proc.stdout.readline()
            if not line:
                break
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("id") == mid:
                return msg
        return {"error": {"message": "timeout"}}

    def tool(self, name: str, args: dict, timeout: float = 90.0) -> dict:
        return self.call("tools/call", {"name": name, "arguments": args}, timeout)

    def stop(self) -> None:
        try:
            self.proc.terminate()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------- observation


def observe(driver: Driver, pid: int, window_id: int, session: str) -> dict:
    args = {
        "pid": pid, "window_id": window_id,
        "include_screenshot": False, "max_elements": 6000,
    }
    if session:
        args["session"] = session
    res = driver.tool("get_window_state", args)
    return res.get("result", {}).get("structuredContent", {}) or {}


def with_session(args: dict, session: str) -> dict:
    if session:
        args["session"] = session
    return args


def window_bounds(state: dict) -> dict:
    return state.get("window_bounds") or {}


def content_bounds(state: dict) -> dict | None:
    """The web-content region of a browser window, so chrome buttons stay out of the table."""
    best = None
    for el in state.get("elements", []):
        if el.get("role") != "AXWebArea":
            continue
        frame = el.get("frame") or {}
        if float(frame.get("w", 0)) < 200 or float(frame.get("h", 0)) < 200:
            continue
        if best is None or float(frame.get("w", 0)) * float(frame.get("h", 0)) > \
                float(best.get("w", 0)) * float(best.get("h", 0)):
            best = frame
    return best


STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "into", "from", "then", "than",
    "click", "open", "page", "using", "left", "right", "sidebar", "button", "link",
    "so", "can", "will", "should", "make", "sure", "about", "must", "need",
}


def goal_tokens(goal: str) -> list[str]:
    out = []
    for word in goal.lower().replace("'s", "").split():
        word = word.strip(".,:;()\"'`[]!?")
        if len(word) > 3 and word not in STOPWORDS:
            out.append(word)
    return out


def relevance(label: str, tokens: list[str]) -> int:
    low = label.lower()
    return sum(1 for t in tokens if t in low)


def element_rows(state: dict, max_regions: int, tokens: list[str] | None = None) -> list[dict]:
    """Visible, labelled, interactive elements, ranked, deduplicated.

    When the window is a browser, only elements inside the web-content area are
    offered: the toolbar, omnibox and extension buttons are never the answer.
    Rows that share a meaningful word with the goal float to the top, because
    the 32-candidate contract cannot hold every control on a rich web app.
    """
    tokens = tokens or []
    bounds = window_bounds(state)
    bx, by = float(bounds.get("x", 0)), float(bounds.get("y", 0))
    bw, bh = float(bounds.get("width", 0)), float(bounds.get("height", 0))
    content = content_bounds(state)
    if content is not None:
        bx, by = float(content.get("x", 0)), float(content.get("y", 0))
        bw, bh = float(content.get("w", 0)), float(content.get("h", 0))
    rows: list[dict] = []
    seen: set[tuple[str, int, int]] = set()
    # macOS sidebars, lists and tables are AXOutline/AXTable -> AXRow -> AXStaticText. The
    # ROW is what you click and it carries no label; the LABEL is on a child static text
    # that is not interactive. Filtering on role alone dropped every sidebar item: on
    # System Settings "Displays" was in the tree and never offered, so Jev answered at
    # 0.35 because the right answer was not on the table.
    #
    # The driver reports `parent_index`, so this follows the real tree. An earlier
    # version guessed from frame overlap, which cannot work for a row scrolled out of
    # view - it has no frame - and that is exactly the row you most need to know about.
    by_index = {el.get("element_index"): el for el in state.get("elements", [])}

    def _row_of(el: dict) -> dict | None:
        """The enclosing row, preferring AXRow over AXCell.

        Stopping at the first AXCell looked right and lost the selection: the tree is
        AXRow -> AXCell -> AXStaticText and `selected` lives on the ROW, so every row
        read as unselected and arrival could never be proved.
        """
        node, hops, cell = el, 0, None
        while node is not None and hops < 5:
            node = by_index.get(node.get("parent_index"))
            if node is None:
                break
            if node.get("role") == "AXRow":
                return node
            if node.get("role") == "AXCell" and cell is None:
                cell = node
            hops += 1
        return cell

    for el in state.get("elements", []):
        label = (el.get("label") or "").strip()
        if not label:
            continue
        role = el.get("role") or ""
        acts = el.get("actions") or []
        row = _row_of(el) if role == "AXStaticText" else None
        if role not in INTERACTIVE_ROLES and "AXPress" not in acts and row is None:
            continue
        selected = bool(el.get("selected"))
        if row is not None:
            role = "AXRow"           # describe it to Jev as what it is: a selectable row
            selected = selected or bool(row.get("selected"))
        frame = el.get("frame") or {}
        x, y = float(frame.get("x", 0)), float(frame.get("y", 0))
        w, h = float(frame.get("w", 0)), float(frame.get("h", 0))
        if w < 8 or h < 8:
            continue
        if bw and bh and not (bx <= x <= bx + bw and by <= y <= by + bh):
            continue
        key = (label[:60], int(x), int(y))
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "token": el.get("element_token"),
            "role": role,
            "label": label[:120],
            "x": x, "y": y, "w": w, "h": h,
            "local_x": x - bx, "local_y": y - by,
            "cx": x - bx + w / 2, "cy": y - by + h / 2,
            "index": el.get("element_index"),
            "selected": selected,
        })
    rows.sort(key=lambda r: (-relevance(r["label"], tokens), r["y"], r["x"]))
    return rows[:max_regions]


def single_line(text: str, limit: int = 78) -> str:
    return " ".join(text.split())[:limit]


def offscreen_matches(state: dict, tokens: list[str], limit: int = 3) -> list[str]:
    """Goal-relevant labels that are in the tree but scrolled out of view.

    A row below the fold has no frame, so it cannot be clicked and is rightly left off
    the table. But leaving Jev ignorant of it is a different mistake: asked to open
    "Sound" with Sound off-screen, it scored 0.33 and the run stalled, because from where
    it sat nothing on the table served the goal. It was right. The fix is not to lower
    the floor until it guesses - it is to say the thing exists further down, so that
    scrolling becomes the obviously correct move instead of a shot in the dark.
    """
    if not tokens:
        return []
    found: list[str] = []
    for el in state.get("elements", []):
        label = (el.get("label") or "").strip()
        if not label or el.get("frame"):
            continue
        if relevance(label, tokens) and label not in found:
            found.append(label[:40])
    return found[:limit]


def _stable_id(kind: str, label: str, used: set[str]) -> str:
    """An id for "this element", not for "this snapshot's handle to it"."""
    slug = re.sub(r"[^a-z0-9]+", "-", _fold(label))[:36].strip("-") or "item"
    base = f"{kind}:{slug}"
    cid, n = base, 2
    while cid in used:
        cid, n = f"{base}-{n}", n + 1
    used.add(cid)
    return cid


def build_table(rows: list[dict], below: list[str] | None = None) -> tuple[list[dict], list[dict]]:
    regions: list[dict] = []
    candidates: list[dict] = []
    used: set[str] = set()
    for i, r in enumerate(rows):
        rid = f"r{i}"
        regions.append({
            "id": rid, "role": r["role"].replace("AX", "").lower(),
            "label": single_line(r["label"]), "interactive": True,
        })
        typing = r["role"] in ("AXTextField", "AXSearchField")
        verb = "Type into" if typing else "Click"
        # The id used to be `click:<element_token>`, and the driver reissues every token
        # on every observation. So no id in `history` was ever still on the table, and
        # Jev had no way to see it had already clicked something: given "open General,
        # then Storage" it clicked General ten times running. An id built from what the
        # element IS survives re-observation, so history means something.
        r["cid"] = _stable_id("type" if typing else "click", r["label"], used)
        state = " It is the currently selected item, so clicking it again changes nothing." \
            if r.get("selected") else ""
        candidates.append({
            "id": r["cid"],
            "description": f"{verb} [{i}] {r['role'].replace('AX','').lower()} "
                           f"\"{single_line(r['label'])}\".{state}",
        })
    for extra, desc in STANDARD_ACTIONS:
        if extra == "scroll-down" and below:
            names = ", ".join(f'"{single_line(b, 30)}"' for b in below)
            desc = f"Scroll down: {names} exists further down this list but is not visible yet."
        candidates.append({"id": extra, "description": desc})
    return regions, candidates


# ---------------------------------------------------------------- text helper

def text_helper(goal: str, field_label: str, values: list[str]) -> str:
    key = os.environ.get("TEXT_MODEL_API_KEY") or os.environ.get("OPENROUTER_API_KEY", "")
    model = os.environ.get("TEXT_MODEL", "google/gemini-2.5-flash")
    base = os.environ.get("TEXT_MODEL_BASE_URL", "https://openrouter.ai/api/v1")
    if not key or not values:
        return values[0] if values else ""
    prompt = (
        "You write one short value to type into a GUI field. Reply as JSON "
        '{"text": "..."} and nothing else.\n'
        f"Goal: {goal}\nField: {field_label}\n"
        f"Allowed values (choose the best one, exactly as written): {values}\n"
    )
    body = json.dumps({
        "model": model, "temperature": 0,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(
        f"{base.rstrip('/')}/chat/completions", data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as fh:
            payload = json.load(fh)
        raw = payload["choices"][0]["message"]["content"].strip()
        raw = raw.strip("`").removeprefix("json").strip()
        return str(json.loads(raw).get("text", "")).strip() or values[0]
    except (urllib.error.URLError, KeyError, ValueError, TimeoutError):
        return values[0]


# ---------------------------------------------------------------- execution


def execute(driver: Driver, pid: int, window_id: int, session: str,
            action: str, rows: list[dict], goal: str, values: list[str]) -> tuple[str, str]:
    """Run exactly one pre-validated action. Returns (op, detail)."""
    if action.startswith("click:"):
        token = next((r["token"] for r in rows if r.get("cid") == action), None)
        for r in rows:
            if r["token"] == token:
                res = driver.tool("click", with_session({
                    "pid": pid, "window_id": window_id,
                    "element_token": token, "delivery_mode": "background",
                }, session))
                return "click", json.dumps(_brief(res))
        return "click", "target token no longer observed"
    if action.startswith("type:"):
        token = next((r["token"] for r in rows if r.get("cid") == action), None)
        for r in rows:
            if r["token"] == token:
                text = text_helper(goal, r["label"], values)
                res = driver.tool("type_text", with_session({
                    "pid": pid, "window_id": window_id,
                    "element_token": token, "text": text,
                    "delivery_mode": "foreground",
                }, session), timeout=120)
                return "type", f"{text!r} -> {json.dumps(_brief(res))}"
        return "type", "target token no longer observed"
    if action in ("scroll-down", "scroll-up"):
        res = driver.tool("scroll", with_session({
            "pid": pid, "window_id": window_id,
            "direction": action.split("-", 1)[1], "amount": 6,
        }, session))
        return "scroll", json.dumps(_brief(res))
    if action == "wait":
        time.sleep(1.0)
        return "wait", "slept 1s"
    return action, "no-op"


def _brief(res: dict) -> dict:
    out = res.get("result", {}) if isinstance(res, dict) else {}
    sc = out.get("structuredContent") or {}
    return {
        "effect": sc.get("effect", out.get("status", "unknown")),
        "message": str(out.get("result") or res.get("error") or "")[:120],
    }


_DASHES = dict.fromkeys(map(ord, "\u2010\u2011\u2012\u2013\u2014\u2212"), "-")


def _fold(text: str) -> str:
    """Compare what a person typed with what an app displays.

    macOS writes "Wi\u2011Fi" with a NON-BREAKING hyphen. `--expect Wi-Fi` never matched
    it, so a click that landed first time at 0.96 confidence was reported unverified and
    the runner clicked it five more times. Fold dash variants and width forms before
    comparing; nobody can see the difference, so the check must not depend on it.
    """
    import unicodedata
    return unicodedata.normalize("NFKC", text or "").translate(_DASHES).casefold().strip()


def verify(rows: list[dict], title: str, expect: str) -> bool:
    """Is the goal state actually reached? Deliberately strict about what counts.

    Two false passes lived here, and the skill's own documented example hit both.

    1. It matched `--expect` against any ELEMENT LABEL. `--expect 'Library'` passed on
       every page of YouTube Music, because the left nav carries a Library link
       everywhere. "A link named X exists" is not "page X is open", and the check ran
       before the first action, so the runner exited 0 having clicked nothing.
    2. An empty `--expect` returned True, and `--expect` defaults to "". Every run
       without it reported PASS regardless of what happened.

    The window title is the one signal that actually changes when you arrive somewhere,
    so it is the only thing accepted. No expectation now means "unverified", not "pass".
    """
    if not expect:
        return False
    needle = _fold(expect)
    if needle in _fold(title or ""):
        return True
    # Some apps never title their window: System Settings reports an empty title on the
    # General pane, so title-only checking could not pass there however right the click.
    # "The row named X is the SELECTED row" is real proof of arrival. It is not the old
    # bug in disguise: that accepted a row named X merely EXISTING, which is true on
    # every page. Selection is only true once you are there.
    return any(r.get("selected") and needle in _fold(r.get("label", "")) for r in rows)


# ---------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Bounded GUI computer-use loop: cua-driver + Jev")
    p.add_argument("--pid", type=int, required=True)
    p.add_argument("--window-id", type=int, required=True)
    p.add_argument("--goal", required=True)
    p.add_argument("--max-steps", type=int, default=10)
    p.add_argument("--max-regions", type=int, default=MAX_REGIONS,
                   help=f"Element rows offered to Jev (max {MAX_REGIONS}: build_table "
                        f"always appends {len(STANDARD_ACTIONS)} standard actions and the "
                        f"contract caps the table at {MAX_CANDIDATES}).")
    p.add_argument("--expect", default="")
    p.add_argument("--values", default="", help="Pipe-separated pool for typed fields.")
    p.add_argument("--session", default="",
                   help="Optional cua-driver session label (omit when the transport has none).")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    # Above the cap the table is rejected by the contract on EVERY step, at step 1,
    # every time - a flag that silently guarantees total failure is worse than no flag.
    regions_cap = max(1, min(args.max_regions, MAX_REGIONS))
    values = [v.strip() for v in args.values.split("|") if v.strip()]
    tokens = goal_tokens(args.goal)

    if jev_choose is None:
        print("FAIL: jevkit not importable from this checkout.")
        return 2
    if is_sensitive(args.goal):
        print("FAIL: the goal looks sensitive; refusing to send it to Jev.")
        return 2

    for k in ("TYPESAFE_API_KEY",):
        if k in os.environ:
            continue
    if not os.environ.get("TYPESAFE_API_KEY"):
        tok = subprocess.run(
            ["security", "find-generic-password", "-s", "Hermes TypeSafe API",
             "-a", "TYPESAFE_API_KEY", "-w"],
            capture_output=True, text=True)
        if tok.returncode == 0 and tok.stdout.strip():
            os.environ["TYPESAFE_API_KEY"] = tok.stdout.strip()
    if not os.environ.get("TEXT_MODEL_API_KEY") and not os.environ.get("OPENROUTER_API_KEY"):
        tok = subprocess.run(
            # No hardcoded account: whoever runs this is the account. A name baked in
            # here works on exactly one machine and fails silently on every other.
            ["security", "find-generic-password", "-s", "OPENROUTER_API_KEY",
             "-a", os.environ.get("USER", ""), "-w"],
            capture_output=True, text=True)
        if tok.returncode == 0 and tok.stdout.strip():
            os.environ["OPENROUTER_API_KEY"] = tok.stdout.strip()
    if not os.environ.get("TYPESAFE_API_KEY"):
        print("FAIL: no TypeSafe credential. Run `jev setup-key`.")
        return 2

    driver = Driver()
    log: list[dict] = []
    history: list[dict] = []
    title = ""
    rows: list[dict] = []
    steps = 0
    abstained = False
    t0 = time.time()
    try:
        if args.session:
            driver.tool("start_session", {"session": args.session})
        stalled = 0
        last_digest: str | None = None
        for step in range(1, args.max_steps + 1):
            state = observe(driver, args.pid, args.window_id, args.session)
            title = state.get("window_title", "")
            rows = element_rows(state, regions_cap, tokens)
            if not rows:
                print(f"  step {step}: no interactive elements observed")
                break
            safe_rows = [r for r in rows if not is_sensitive(r["label"])]
            withheld = len(rows) - len(safe_rows)
            if withheld:
                print(f"  step {step}: withheld {withheld} label(s) that look sensitive")
            rows = safe_rows
            if not rows:
                print("  every observed label looks sensitive; stopping")
                abstained = True
                break
            digest = "|".join(f"{r['label'][:40]}@{int(r['x'])},{int(r['y'])}" for r in rows)
            stalled = stalled + 1 if digest == last_digest else 0
            last_digest = digest
            if args.expect and verify(rows, title, args.expect):
                steps = step - 1
                print(f"  verified before step {step}; stopping")
                break
            regions, candidates = build_table(rows, offscreen_matches(state, tokens))
            request = {
                "schema": "jev.action_choice_request_v1",
                "goal": args.goal,
                "observation_id": f"obs-{step}",
                "regions": regions,
                "history": history[-6:],
                "candidates": candidates,
            }
            try:
                t_jev = time.time()
                reply = jev_choose(request)
                jev_ms = int((time.time() - t_jev) * 1000)
            except ValueError as exc:
                print(f"  step {step}: request rejected by the Jev contract: {exc}")
                break
            action = reply.get("selected_id", "reobserve")
            confidence = reply.get("confidence")
            steps = step
            if action in ("done", "abstain"):
                print(f"  step {step:>2}  Jev -> {action} "
                      f"(conf {confidence}) {reply.get('reason','')}")
                abstained = action == "abstain"
                history.append({"selected_id": action, "outcome": "loop ended"})
                break
            if action == "reobserve" and stalled >= 2:
                print(f"  step {step:>2}  nothing on screen is changing after "
                      f"{stalled} reobservations; stopping instead of spinning")
                break
            t1 = time.time()
            op, detail = execute(driver, args.pid, args.window_id, args.session,
                                 action, rows, args.goal, values)
            action_ms = int((time.time() - t1) * 1000)
            print(f"  step {step:>2}  jev {jev_ms:>4} ms + act {action_ms:>4} ms  op={op}  "
                  f"conf={confidence}  {detail[:100]}")
            # These were one field called `decision_ms` that actually timed the CLICK. It
            # made a 470 ms Jev decision look like 2.6 s and sent the latency hunt after
            # the wrong component: the time is the driver confirming the click's effect.
            log.append({"step": step, "action": action, "op": op,
                        "detail": detail, "confidence": confidence,
                        "decision_ms": jev_ms, "action_ms": action_ms})
            what = next((f'{r["role"].replace("AX", "").lower()} "{single_line(r["label"], 40)}"'
                         for r in rows if r.get("cid") == action), action)
            history.append({"selected_id": action,
                            "outcome": f"{op} {what} - {single_line(detail, 60)}"})
            if op == "no-op":
                time.sleep(0.3)
            if not action.startswith(("click:", "type:")):
                continue
    finally:
        try:
            final = observe(driver, args.pid, args.window_id, args.session)
            title = final.get("window_title", title)
            rows = element_rows(final, regions_cap, tokens)
        except Exception:  # noqa: BLE001
            pass
        driver.stop()

    verified = verify(rows, title, args.expect)
    result = {
        "schema": "hermes.computer_use_jev_run_v1",
        "goal": args.goal,
        "window_title": title,
        "steps": steps,
        "elapsed_ms": int((time.time() - t0) * 1000),
        "expected": args.expect,
        "verified": verified,
        "abstained": abstained,
        "regions_last": len(rows),
        "actions": log,
    }
    print(f"  window_title: {title!r}")
    print(f"  independent check for {args.expect!r}: {'PASS' if verified else 'FAIL'}")
    print(f"  {steps} steps, {result['elapsed_ms']} ms total")
    if args.json:
        print(json.dumps(result))
    if abstained:
        return 6
    return 0 if verified else 4


if __name__ == "__main__":
    sys.exit(main())
