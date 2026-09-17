#!/usr/bin/env python3
"""TUI state: filter, click, draw compact frames, jump_here wiring."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import herdr_session_history as h  # noqa: E402


def make_tui(turns: list[h.Turn] | None = None) -> h.HistoryTUI:
    tui = h.HistoryTUI.__new__(h.HistoryTUI)
    tui.target = "w1:p1"
    tui.query = ""
    tui.searching = False
    tui.cursor = 0
    tui.scroll = 0
    tui.status = ""
    tui.body_top = 0
    tui.source_mtime = 0.0
    tui.turns = turns or [
        h.Turn(0, "hello world", "hello world"),
        h.Turn(1, "open session history", "open session history"),
        h.Turn(2, "当前这个历史栏怎么关掉呢？", "当前这个历史栏怎么关掉呢？", current=True),
    ]
    tui.filtered = list(tui.turns)
    tui.viewed_index = tui.turns[-1].index
    tui.scrollback_primed = False
    tui.primed_session = ""
    tui.pending_jump = False
    tui.last_move_at = 0.0
    return tui


class FilterTests(unittest.TestCase):
    def test_search_filters_title_and_preview(self) -> None:
        tui = make_tui()
        tui.query = "历史"
        tui.apply_filter()
        self.assertEqual([t.index for t in tui.filtered], [2])
        self.assertEqual(tui.cursor, 0)

    def test_search_is_case_insensitive(self) -> None:
        tui = make_tui()
        tui.query = "HELLO"
        tui.apply_filter()
        self.assertEqual([t.title for t in tui.filtered], ["hello world"])

    def test_empty_search_restores_all(self) -> None:
        tui = make_tui()
        tui.query = "history"
        tui.apply_filter()
        tui.query = ""
        tui.apply_filter()
        self.assertEqual(len(tui.filtered), 3)

    def test_jump_uses_turn_index_not_filtered_row(self) -> None:
        tui = make_tui()
        tui.query = "关掉"
        tui.apply_filter()
        tui.cursor = 0
        calls: list[tuple] = []

        def fake_jump(pane, to_index, turn_count, primed, primed_session=""):
            calls.append((pane, to_index, turn_count, primed))
            return True

        orig = h.jump_scrollback
        h.jump_scrollback = fake_jump  # type: ignore[assignment]
        self.addCleanup(lambda: setattr(h, "jump_scrollback", orig))
        tui.jump_here()
        self.assertEqual(calls, [("w1:p1", 2, 3, False)])
        self.assertEqual(tui.viewed_index, 2)
        self.assertIn("#3/3", tui.status)

    def test_jump_failure_stays_on_status_not_crash(self) -> None:
        tui = make_tui()

        def boom(*_args, **_kwargs):
            raise RuntimeError("no scroll jump for agy")

        orig = h.jump_scrollback
        h.jump_scrollback = boom  # type: ignore[assignment]
        self.addCleanup(lambda: setattr(h, "jump_scrollback", orig))
        tui.jump_here()
        self.assertIn("no scroll jump", tui.status)

    def test_empty_list_jump_is_noop(self) -> None:
        tui = make_tui([])
        tui.filtered = []
        tui.jump_here()
        self.assertEqual(tui.status, "")


class DrawFrameTests(unittest.TestCase):
    def test_compressed_draw_keeps_titles(self) -> None:
        class Fake:
            def __init__(self, height: int, width: int) -> None:
                self.height, self.width = height, width
                self.grid = [[" "] * width for _ in range(height)]

            def getmaxyx(self):
                return self.height, self.width

            def erase(self):
                self.grid = [[" "] * self.width for _ in range(self.height)]

            def addstr(self, y, x, text, attr=0):
                for i, ch in enumerate(text or ""):
                    if 0 <= y < self.height and 0 <= x + i < self.width:
                        self.grid[y][x + i] = ch

            def refresh(self):
                pass

            def resize(self, height, width):
                self.height, self.width = height, width

        orig = h.sync_curses_size
        h.sync_curses_size = lambda stdscr: (stdscr.height, stdscr.width)  # type: ignore[assignment]
        self.addCleanup(lambda: setattr(h, "sync_curses_size", orig))
        fake = Fake(8, 16)
        tui = make_tui()
        tui.stdscr = fake  # type: ignore[assignment]
        tui.cursor = 2
        tui.draw()
        text = "\n".join("".join(row) for row in fake.grid)
        self.assertIn("历史", text)
        self.assertIn("hello", text.lower())

    def test_empty_pane_message(self) -> None:
        class Fake:
            def __init__(self) -> None:
                self.height, self.width = 10, 40
                self.grid = [[" "] * 40 for _ in range(10)]

            def getmaxyx(self):
                return self.height, self.width

            def erase(self):
                self.grid = [[" "] * self.width for _ in range(self.height)]

            def addstr(self, y, x, text, attr=0):
                for i, ch in enumerate(text or ""):
                    if 0 <= y < self.height and 0 <= x + i < self.width:
                        self.grid[y][x + i] = ch

            def refresh(self):
                pass

        orig = h.sync_curses_size
        h.sync_curses_size = lambda stdscr: (stdscr.height, stdscr.width)  # type: ignore[assignment]
        self.addCleanup(lambda: setattr(h, "sync_curses_size", orig))
        tui = make_tui([])
        tui.filtered = []
        tui.stdscr = Fake()  # type: ignore[assignment]
        tui.draw()
        text = "\n".join("".join(row) for row in tui.stdscr.grid)
        self.assertIn("no turns in this pane", text)


class DisplayWidthTests(unittest.TestCase):
    def test_cjk_clip_and_pad(self) -> None:
        self.assertEqual(h.display_width("历史"), 4)
        clipped = h.clip("当前这个历史栏怎么关掉呢？", 8)
        self.assertLessEqual(h.display_width(clipped), 8)
        padded = h.pad("历史", 8)
        self.assertEqual(h.display_width(padded), 8)

    def test_clip_empty_width(self) -> None:
        self.assertEqual(h.clip("hello", 0), "")
        self.assertEqual(h.pad("hello", 0), "")

    def test_row_label_never_drops_title_when_wide_enough(self) -> None:
        label = h.row_label("Install Herdr skill", False, False, 16)
        self.assertIn("Install", label)
        tiny = h.row_label("Install Herdr skill", True, True, 2)
        self.assertTrue(tiny.strip())


class CardSnippetTests(unittest.TestCase):
    def test_blank_lines_skipped_and_capped(self) -> None:
        tui = make_tui()
        turn = h.Turn(0, "t", "a\n\nb\nc\nd\ne")
        parts = tui.card_snippets(turn, 20)
        self.assertEqual(parts, ["a", "b", "c", "d"])

    def test_empty_preview(self) -> None:
        tui = make_tui()
        self.assertEqual(tui.card_snippets(h.Turn(0, "t", ""), 20), ["(empty)"])


if __name__ == "__main__":
    unittest.main()
