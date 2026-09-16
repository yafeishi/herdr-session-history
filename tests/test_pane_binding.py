#!/usr/bin/env python3
"""Per-pane rail binding, reuse, and left-dock decisions."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import herdr_session_history as h  # noqa: E402


class InvokerPaneTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env = os.environ.copy()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env)

    def test_env_target_wins(self) -> None:
        os.environ["HERDR_SESSION_HISTORY_TARGET"] = "w1:p11"
        os.environ["HERDR_PLUGIN_CONTEXT_JSON"] = json.dumps({"focused_pane_id": "w1:p1"})
        self.assertEqual(h.invoker_pane(), "w1:p11")

    def test_context_agent_pane_when_env_missing(self) -> None:
        os.environ.pop("HERDR_SESSION_HISTORY_TARGET", None)
        os.environ["HERDR_PLUGIN_CONTEXT_JSON"] = json.dumps({"focused_pane_id": "w1:p11"})
        orig = h.pane_record
        h.pane_record = lambda pid: {"agent": "grok"} if pid == "w1:p11" else {}  # type: ignore[assignment]
        self.addCleanup(lambda: setattr(h, "pane_record", orig))
        self.assertEqual(h.invoker_pane(), "w1:p11")

    def test_target_file_fallback(self) -> None:
        os.environ.pop("HERDR_SESSION_HISTORY_TARGET", None)
        os.environ["HERDR_PLUGIN_CONTEXT_JSON"] = "{}"
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["HERDR_PLUGIN_STATE_DIR"] = tmp
            Path(tmp, "target_pane").write_text("w1:p5\n")
            self.assertEqual(h.invoker_pane(), "w1:p5")

    def test_invalid_context_json_is_empty(self) -> None:
        os.environ["HERDR_PLUGIN_CONTEXT_JSON"] = "{not json"
        self.assertEqual(h.context_json(), {})


class WorkspaceCwdTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env = os.environ.copy()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env)

    def test_env_overrides_context(self) -> None:
        os.environ["HERDR_SESSION_HISTORY_CWD"] = "~/proj"
        os.environ["HERDR_PLUGIN_CONTEXT_JSON"] = json.dumps({"workspace_cwd": "/other"})
        self.assertEqual(h.workspace_cwd(), str(Path("~/proj").expanduser()))

    def test_reads_workspace_then_pane(self) -> None:
        os.environ.pop("HERDR_SESSION_HISTORY_CWD", None)
        os.environ["HERDR_PLUGIN_CONTEXT_JSON"] = json.dumps(
            {"workspace": {"identity_cwd": "/ws"}, "pane": {"cwd": "/pane"}}
        )
        self.assertEqual(h.workspace_cwd(), "/ws")


class ReuseAndDockTests(unittest.TestCase):
    def test_reuse_only_when_bound_to_same_pane(self) -> None:
        self.assertTrue(h.should_reuse_history("w1:p0", "w1:p1", "w1:p1"))
        self.assertFalse(h.should_reuse_history("w1:p0", "w1:p1", "w1:p11"))
        self.assertFalse(h.should_reuse_history("", "w1:p1", "w1:p1"))
        self.assertFalse(h.should_reuse_history("w1:p0", "w1:p1", ""))

    def test_two_agent_panes_in_one_tab_need_two_rails(self) -> None:
        """Same tab, different panes: opening p11 must not reuse p1's rail."""
        existing, bound = h.parse_binding(h.format_binding("w1:p0", "w1:p1"))
        self.assertFalse(h.should_reuse_history(existing, bound, "w1:p11"))

    def test_swap_when_history_opened_to_the_right(self) -> None:
        self.assertTrue(h.should_swap_to_left(56, 0))
        self.assertFalse(h.should_swap_to_left(0, 56))
        self.assertFalse(h.should_swap_to_left(10, 10))


class PickIdTests(unittest.TestCase):
    def test_nested_plugin_open_payload(self) -> None:
        payload = {"plugin_pane": {"pane": {"pane_id": "w1:p14"}}}
        inner = (payload.get("plugin_pane") or {}).get("pane") or {}
        self.assertEqual(h.pick_id(inner, "pane_id", "id"), "w1:p14")

    def test_non_dict_is_empty(self) -> None:
        self.assertEqual(h.pick_id(None, "id"), "")
        self.assertEqual(h.pick_id(["w1:p1"], "id"), "")


class BindingFileTests(unittest.TestCase):
    def test_whitespace_and_empty(self) -> None:
        self.assertEqual(h.parse_binding("  w1:p0\tw1:p1\n"), ("w1:p0", "w1:p1"))
        self.assertEqual(h.parse_binding(""), ("", ""))
        self.assertEqual(h.parse_binding(None), ("", ""))  # type: ignore[arg-type]


class JumpScrollbackIntegrationTests(unittest.TestCase):
    def test_sends_planned_keys_to_bound_pane(self) -> None:
        sent: list[tuple] = []

        def fake_herdr(*args: str):
            sent.append(args)
            return {}

        orig_herdr = h.herdr
        orig_record = h.pane_record
        orig_pause = h.JUMP_KEY_PAUSE
        h.herdr = fake_herdr  # type: ignore[assignment]
        h.pane_record = lambda pid: {"agent": "grok", "agent_status": "idle"}  # type: ignore[assignment]
        h.JUMP_KEY_PAUSE = 0
        self.addCleanup(lambda: setattr(h, "herdr", orig_herdr))
        self.addCleanup(lambda: setattr(h, "pane_record", orig_record))
        self.addCleanup(lambda: setattr(h, "JUMP_KEY_PAUSE", orig_pause))

        primed = h.jump_scrollback("w1:p1", 0, 3, False)
        self.assertTrue(primed)
        agent_calls = [call for call in sent if call[:3] == ("agent", "send-keys", "w1:p1")]
        self.assertEqual(len(agent_calls), 1)
        keys = list(agent_calls[0][3:])
        self.assertEqual(keys[0], "tab")
        self.assertEqual(keys.count("shift+right"), 3)
        self.assertEqual(keys.count("shift+left"), 2)
        self.assertTrue(all(call[2] == "w1:p1" for call in sent if call[0] == "agent"))

    def test_does_not_send_to_sibling_pane(self) -> None:
        sent: list[str] = []
        h_orig = h.herdr
        rec_orig = h.pane_record
        pause = h.JUMP_KEY_PAUSE
        h.herdr = lambda *args: sent.append(args[2]) or {}  # type: ignore[assignment]
        h.pane_record = lambda pid: {"agent": "grok", "agent_status": "working"}  # type: ignore[assignment]
        h.JUMP_KEY_PAUSE = 0
        self.addCleanup(lambda: setattr(h, "herdr", h_orig))
        self.addCleanup(lambda: setattr(h, "pane_record", rec_orig))
        self.addCleanup(lambda: setattr(h, "JUMP_KEY_PAUSE", pause))
        h.jump_scrollback("w1:p11", 1, 2, True)
        self.assertTrue(sent)
        self.assertTrue(all(pane == "w1:p11" for pane in sent))


class MainTests(unittest.TestCase):
    def test_unknown_command(self) -> None:
        with mock.patch.object(sys, "stderr", mock.Mock()):
            self.assertEqual(h.main(["herdr_session_history.py", "nope"]), 2)


if __name__ == "__main__":
    unittest.main()
