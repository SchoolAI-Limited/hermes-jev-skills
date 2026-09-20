"""The GUI runner shipped with no tests and three ways to report success falsely.

Every test here is a bug that was live in a released version, not a hypothetical.
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "jev_gui_agent", REPO / "skills" / "jev-computer-use" / "scripts" / "jev_gui_agent.py")
gui = importlib.util.module_from_spec(_spec)
sys.modules["jev_gui_agent"] = gui
_spec.loader.exec_module(gui)


class VerifyTests(unittest.TestCase):
    """`verify` decides the exit code, so a lenient one turns every run into a pass."""

    def test_a_matching_element_label_is_not_proof_the_page_is_open(self):
        """The skill's own example: --expect 'Library' on YouTube Music, whose left nav
        carries a Library link on every page. It passed without clicking anything."""
        rows = [{"label": "Library", "role": "AXLink"}, {"label": "Home", "role": "AXLink"}]
        self.assertFalse(gui.verify(rows, "Home - YouTube Music", "Library"))

    def test_the_window_title_is_proof(self):
        self.assertTrue(gui.verify([], "Library - YouTube Music", "Library"))

    def test_no_expectation_is_unverified_not_verified(self):
        """--expect defaults to "", so this made every run without it report PASS."""
        self.assertFalse(gui.verify([{"label": "anything"}], "Any Window", ""))

    def test_matching_is_case_insensitive(self):
        self.assertTrue(gui.verify([], "LIBRARY - Music", "library"))


class CandidateBudgetTests(unittest.TestCase):
    """Above the cap the contract rejects the table on every step, at step 1, forever."""

    def _rows(self, n):
        return [{"label": f"item {i}", "role": "AXButton", "token": f"tok{i}"} for i in range(n)]

    def test_the_default_budget_lands_exactly_on_the_contract_limit(self):
        _, candidates = gui.build_table(self._rows(gui.MAX_REGIONS))
        self.assertEqual(len(candidates), gui.MAX_CANDIDATES)

    def test_the_budget_is_derived_not_hardcoded(self):
        self.assertEqual(gui.MAX_REGIONS, gui.MAX_CANDIDATES - len(gui.STANDARD_ACTIONS))

    def test_every_standard_action_is_offered(self):
        _, candidates = gui.build_table(self._rows(3))
        ids = {c["id"] for c in candidates}
        for name, _ in gui.STANDARD_ACTIONS:
            self.assertIn(name, ids)
        # Without these two the chooser has no safe way out of a screen it cannot read.
        self.assertIn("reobserve", ids)
        self.assertIn("abstain", ids)


class PortabilityTests(unittest.TestCase):
    def test_no_hardcoded_home_or_account_in_the_runner(self):
        """check_release blocks /Users/... paths but not a bare account name, and one
        was shipped: the Keychain lookup hardcoded `-a vibex`."""
        import ast
        path = REPO / "skills" / "jev-computer-use" / "scripts" / "jev_gui_agent.py"
        tree = ast.parse(path.read_text())
        # Prose may name the trap it exists to prevent; a string the code USES may not.
        # Docstrings are documentation, so they are excluded via the AST rather than by
        # guessing at comment syntax.
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                doc = ast.get_docstring(node, clean=False)
                if doc:
                    docstrings.add(doc)
        offenders = [
            node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and node.value not in docstrings
            and ("vibex" in node.value or node.value.startswith("/Users/"))
        ]
        self.assertEqual(offenders, [], "machine-specific value in executable code")

    def test_the_driver_is_resolved_not_assumed(self):
        self.assertTrue(callable(gui._find_driver))


if __name__ == "__main__":
    unittest.main()


class SidebarRowTests(unittest.TestCase):
    """macOS sidebars are AXOutline -> AXRow -> AXStaticText. The row is clickable and
    unlabelled; the label sits on a non-interactive child. Filtering on role alone dropped
    every sidebar item, so on System Settings "Displays" was in the tree and never offered
    - Jev answered at 0.35 confidence because the right answer was not on the table.
    With the pairing it answered at 0.90 and the click verified.
    """

    STATE = {"elements": [
        {"role": "AXRow", "frame": {"x": 949, "y": 880, "w": 215, "h": 32},
         "actions": ["AXShowDefaultUI"]},
        {"role": "AXStaticText", "label": "Displays", "element_token": "t1",
         "frame": {"x": 963, "y": 884, "w": 78, "h": 24}, "actions": ["AXShowMenu"]},
        {"role": "AXStaticText", "label": "Just a caption", "element_token": "t2",
         "frame": {"x": 100, "y": 100, "w": 120, "h": 20}, "actions": []},
    ]}

    def test_a_label_inside_a_row_is_offered_as_that_row(self):
        rows = gui.element_rows(self.STATE, 26, [])
        offered = {r["label"]: r for r in rows}
        self.assertIn("Displays", offered)
        self.assertEqual(offered["Displays"]["role"], "AXRow")
        self.assertEqual(offered["Displays"]["token"], "t1")

    def test_loose_static_text_is_still_not_clickable(self):
        """Otherwise every caption on screen becomes a candidate and floods the table."""
        rows = gui.element_rows(self.STATE, 26, [])
        self.assertNotIn("Just a caption", {r["label"] for r in rows})


class InstalledLocationTests(unittest.TestCase):
    def test_jevkit_is_found_from_an_installed_skill_directory(self):
        """Walking parent directories only works inside the checkout. Installed under
        ~/.hermes/skills/ - the only place an agent runs it - every call failed with
        "jevkit not importable". It passed every test because every test ran from the
        checkout, which is the whole lesson."""
        import os, tempfile
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "hermes"
            (home / "plugins" / "hermes-jev" / "jevkit").mkdir(parents=True)
            (home / "plugins" / "hermes-jev" / "jevkit" / "choose.py").write_text("")
            profile = home / "profiles" / "donna"
            profile.mkdir(parents=True)
            saved = os.environ.get("HERMES_HOME")
            try:
                os.environ["HERMES_HOME"] = str(profile)     # a PROFILE dir, as in production
                found = gui._repo_root()
            finally:
                if saved is None:
                    os.environ.pop("HERMES_HOME", None)
                else:
                    os.environ["HERMES_HOME"] = saved
        self.assertIsNotNone(found)
