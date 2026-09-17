#!/usr/bin/env python3
"""High-severity product fixes: per-pane bindings, CJK search, jump debounce."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import herdr_session_history as h  # noqa: E402


class PerPaneBindingStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env = os.environ.copy()
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["HERDR_PLUGIN_STATE_DIR"] = self.tmp.name

    def tearDown(self) -> None:
        self.tmp.cleanup()
        os.environ.clear()
        os.environ.update(self._env)

    def test_two_targets_keep_independent_files(self) -> None:
        h.write_binding("w1:p0", "w1:p1")
        h.write_binding("w1:p12", "w1:p11")
        self.assertEqual(h.read_binding_for("w1:p1"), ("w1:p0", "w1:p1"))
        self.assertEqual(h.read_binding_for("w1:p11"), ("w1:p12", "w1:p11"))
        self.assertTrue(h.should_reuse_history(*h.read_binding_for("w1:p1"), "w1:p1"))
        self.assertFalse(h.should_reuse_history(*h.read_binding_for("w1:p1"), "w1:p11"))

    def test_forget_one_rail_does_not_delete_the_other(self) -> None:
        h.write_binding("w1:p0", "w1:p1")
        h.write_binding("w1:p12", "w1:p11")
        h.forget_binding("w1:p1", "w1:p0")
        self.assertEqual(h.read_binding_for("w1:p1"), ("", ""))
        self.assertEqual(h.read_binding_for("w1:p11"), ("w1:p12", "w1:p11"))

    def test_forget_does_not_unlink_foreign_rail(self) -> None:
        h.write_binding("w1:p0", "w1:p1")
        h.forget_binding("w1:p1", "w1:p99")
        self.assertEqual(h.read_binding_for("w1:p1"), ("w1:p0", "w1:p1"))

    def test_slug_is_filesystem_safe(self) -> None:
        self.assertEqual(h.pane_id_slug("w1:p1"), "w1_p1")
        self.assertNotIn(":", h.pane_id_slug("w1:p11"))


class SearchInputTests(unittest.TestCase):
    def test_accepts_cjk_and_ascii(self) -> None:
        self.assertEqual(h.decode_search_input("历"), "历")
        self.assertEqual(h.decode_search_input("史"), "史")
        self.assertEqual(h.decode_search_input(ord("a")), "a")
        self.assertEqual(h.decode_search_input("/"), "/")

    def test_ignores_control_keys(self) -> None:
        self.assertIsNone(h.decode_search_input(27))
        self.assertIsNone(h.decode_search_input("\x1b"))
        self.assertIsNone(h.decode_search_input(curses_key_up()))


class JumpPrimeTests(unittest.TestCase):
    def test_new_session_invalidates_prime(self) -> None:
        self.assertFalse(
            h.jump_still_primed(
                primed=True,
                session_id="sess-b",
                primed_session="sess-a",
                target_focused=False,
                status="idle",
            )
        )

    def test_user_returning_to_prompt_invalidates_prime(self) -> None:
        self.assertFalse(
            h.jump_still_primed(
                primed=True,
                session_id="sess-a",
                primed_session="sess-a",
                target_focused=True,
                status="idle",
            )
        )

    def test_working_keeps_prime_even_if_focused(self) -> None:
        self.assertTrue(
            h.jump_still_primed(
                primed=True,
                session_id="sess-a",
                primed_session="sess-a",
                target_focused=True,
                status="working",
            )
        )

    def test_idle_unfocused_keeps_prime(self) -> None:
        self.assertTrue(
            h.jump_still_primed(
                primed=True,
                session_id="sess-a",
                primed_session="sess-a",
                target_focused=False,
                status="idle",
            )
        )

    def test_stale_prime_sends_tab_again(self) -> None:
        sent: list[tuple] = []
        orig_herdr = h.herdr
        orig_record = h.pane_record
        h.herdr = lambda *args: sent.append(args) or {}  # type: ignore[assignment]
        h.pane_record = lambda pid: {  # type: ignore[assignment]
            "agent": "grok",
            "agent_status": "idle",
            "focused": True,
            "agent_session": {"value": "sess-2"},
        }
        pause = h.JUMP_KEY_PAUSE
        h.JUMP_KEY_PAUSE = 0
        self.addCleanup(lambda: setattr(h, "herdr", orig_herdr))
        self.addCleanup(lambda: setattr(h, "pane_record", orig_record))
        self.addCleanup(lambda: setattr(h, "JUMP_KEY_PAUSE", pause))
        h.jump_scrollback("w1:p1", 0, 2, True, "sess-1")
        keys = list(sent[0][3:])
        self.assertEqual(keys[0], "tab")


class JumpDebounceTests(unittest.TestCase):
    def test_flush_only_after_idle(self) -> None:
        self.assertFalse(h.should_flush_jump(True, 0.05, 0.2))
        self.assertTrue(h.should_flush_jump(True, 0.21, 0.2))
        self.assertFalse(h.should_flush_jump(False, 1.0, 0.2))

    def test_arrow_move_does_not_jump_until_flush(self) -> None:
        tui = h.HistoryTUI.__new__(h.HistoryTUI)
        tui.target = "w1:p1"
        tui.turns = [h.Turn(0, "a", "a"), h.Turn(1, "b", "b")]
        tui.filtered = list(tui.turns)
        tui.cursor = 1
        tui.pending_jump = False
        tui.last_move_at = 0.0
        tui.scrollback_primed = False
        tui.primed_session = ""
        tui.status = ""
        tui.viewed_index = 1
        jumps: list[int] = []

        def fake_jump(*_args, **_kwargs):
            jumps.append(tui.cursor)
            return True

        orig = h.jump_scrollback
        h.jump_scrollback = fake_jump  # type: ignore[assignment]
        self.addCleanup(lambda: setattr(h, "jump_scrollback", orig))
        tui.note_cursor_move()
        self.assertTrue(tui.pending_jump)
        self.assertEqual(jumps, [])
        tui.last_move_at = 0.0
        tui.flush_jump_if_idle()
        self.assertEqual(jumps, [1])
        self.assertFalse(tui.pending_jump)


class BatchedSendTests(unittest.TestCase):
    def test_one_call_for_short_chord(self) -> None:
        sent: list[tuple] = []
        orig = h.herdr
        h.herdr = lambda *args: sent.append(args) or {}  # type: ignore[assignment]
        self.addCleanup(lambda: setattr(h, "herdr", orig))
        h.send_keys_paced("w1:p1", ["tab", "shift+right", "shift+left"], pause=0)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0], ("agent", "send-keys", "w1:p1", "tab", "shift+right", "shift+left"))


def curses_key_up() -> int:
    import curses

    return int(getattr(curses, "KEY_UP", 259))


if __name__ == "__main__":
    unittest.main()
