#!/usr/bin/env python3
"""History rail should die with the bound agent pane."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import herdr_session_history as h  # noqa: E402

NOT_FOUND = '{"error":{"code":"pane_not_found","message":"pane w1:p11 not found"},"id":"cli:pane:get"}'


class HerdrResultTests(unittest.TestCase):
    def test_success_returns_inner_result(self) -> None:
        fake = mock.Mock(returncode=0, stdout='{"id":"cli:pane:get","result":{"pane":{"pane_id":"w1:p1"}}}\n', stderr="")
        with mock.patch.object(h.subprocess, "run", return_value=fake):
            self.assertEqual(h.herdr("pane", "get", "w1:p1"), {"pane": {"pane_id": "w1:p1"}})

    def test_nonzero_raises_with_json_error(self) -> None:
        fake = mock.Mock(returncode=1, stdout="", stderr=NOT_FOUND)
        with mock.patch.object(h.subprocess, "run", return_value=fake):
            with self.assertRaises(RuntimeError) as ctx:
                h.herdr("pane", "get", "w1:p999")
            self.assertEqual(h.herdr_error_code(str(ctx.exception)), "pane_not_found")


class ErrorCodeTests(unittest.TestCase):
    def test_parses_herdr_json_error(self) -> None:
        self.assertEqual(h.herdr_error_code(NOT_FOUND), "pane_not_found")
        self.assertTrue(h.pane_is_gone("pane_not_found"))

    def test_ignores_transient_errors(self) -> None:
        timeout = '{"error":{"code":"timeout","message":"timed out"}}'
        self.assertEqual(h.herdr_error_code(timeout), "timeout")
        self.assertFalse(h.pane_is_gone("timeout"))
        self.assertFalse(h.pane_is_gone(""))

    def test_plain_text_not_found(self) -> None:
        self.assertEqual(h.herdr_error_code("pane w1:p9 not found"), "pane_not_found")


class TargetStillOpenTests(unittest.TestCase):
    def test_missing_id_is_closed(self) -> None:
        self.assertFalse(h.target_still_open(""))

    def test_pane_not_found_closes_rail(self) -> None:
        def fake(*_args: str):
            raise RuntimeError(NOT_FOUND)

        orig = h.herdr
        h.herdr = fake  # type: ignore[assignment]
        self.addCleanup(lambda: setattr(h, "herdr", orig))
        self.assertFalse(h.target_still_open("w1:p11"))

    def test_timeout_keeps_rail(self) -> None:
        def fake(*_args: str):
            raise RuntimeError('{"error":{"code":"unavailable","message":"socket"}}')

        orig = h.herdr
        h.herdr = fake  # type: ignore[assignment]
        self.addCleanup(lambda: setattr(h, "herdr", orig))
        self.assertTrue(h.target_still_open("w1:p1"))

    def test_live_pane_stays_open(self) -> None:
        orig = h.herdr
        h.herdr = lambda *args: {"pane": {"pane_id": "w1:p1", "tab_id": "w1:t1"}}  # type: ignore[assignment]
        self.addCleanup(lambda: setattr(h, "herdr", orig))
        self.assertTrue(h.target_still_open("w1:p1"))


class FollowCloseLoopTests(unittest.TestCase):
    def test_timeout_tick_exits_when_target_is_gone(self) -> None:
        tui = h.HistoryTUI.__new__(h.HistoryTUI)
        tui.target = "w1:p11"
        tui.stdscr = mock.Mock()
        tui.stdscr.getch.return_value = -1
        tui.stdscr.getmaxyx.return_value = (20, 40)
        tui.query = ""
        tui.searching = False
        tui.cursor = 0
        tui.scroll = 0
        tui.status = ""
        tui.body_top = 0
        tui.source_mtime = 0.0
        tui.turns = []
        tui.filtered = []
        tui.viewed_index = 0
        tui.scrollback_primed = False
        tui.draw = lambda: None  # type: ignore[assignment]

        orig_open = h.target_still_open
        orig_close = h.close_own_pane
        closed = []
        h.target_still_open = lambda pid: False  # type: ignore[assignment]
        h.close_own_pane = lambda: closed.append(True)  # type: ignore[assignment]
        self.addCleanup(lambda: setattr(h, "target_still_open", orig_open))
        self.addCleanup(lambda: setattr(h, "close_own_pane", orig_close))

        with mock.patch.object(h.curses, "curs_set"), mock.patch.object(
            h.curses, "use_default_colors"
        ), mock.patch.object(h.curses, "mousemask"):
            tui.run()
        self.assertEqual(closed, [True])


if __name__ == "__main__":
    unittest.main()
