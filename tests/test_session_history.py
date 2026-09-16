#!/usr/bin/env python3
"""Unit tests for herdr-session-history (no live Herdr required)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from herdr_session_history import (  # noqa: E402
    click_index,
    format_binding,
    layout_for,
    parse_binding,
    plan_scroll_jump,
    row_label,
)


class PlanScrollJumpTests(unittest.TestCase):
    def test_idle_unprimed_jumps_to_first_turn_absolutely(self) -> None:
        plan = plan_scroll_jump(
            kind="grok",
            status="idle",
            to_index=0,
            turn_count=5,
            primed=False,
        )
        self.assertEqual(plan.skip_reason, "")
        self.assertTrue(plan.primed)
        self.assertEqual(plan.keys[0], "tab")
        self.assertEqual(plan.keys.count("shift+right"), 5)
        self.assertEqual(plan.keys.count("shift+left"), 4)
        self.assertEqual(plan.keys[-4:], ("shift+left",) * 4)

    def test_idle_primed_does_not_toggle_tab(self) -> None:
        plan = plan_scroll_jump(
            kind="grok",
            status="idle",
            to_index=2,
            turn_count=5,
            primed=True,
        )
        self.assertNotIn("tab", plan.keys)
        self.assertEqual(plan.keys.count("shift+right"), 5)
        self.assertEqual(plan.keys.count("shift+left"), 2)

    def test_working_still_jumps_without_tab(self) -> None:
        """Grok parks keys in scrollback while answering; Tab would leave it."""
        plan = plan_scroll_jump(
            kind="grok",
            status="working",
            to_index=0,
            turn_count=8,
            primed=False,
        )
        self.assertEqual(plan.skip_reason, "")
        self.assertNotIn("tab", plan.keys)
        self.assertEqual(plan.keys.count("shift+right"), 8)
        self.assertEqual(plan.keys.count("shift+left"), 7)

    def test_last_turn_does_not_walk_left(self) -> None:
        plan = plan_scroll_jump(
            kind="grok",
            status="idle",
            to_index=4,
            turn_count=5,
            primed=True,
        )
        self.assertEqual(plan.keys, ("shift+right",) * 5)
        self.assertEqual(plan.keys.count("shift+left"), 0)

    def test_same_turn_twice_is_idempotent(self) -> None:
        a = plan_scroll_jump(kind="grok", status="idle", to_index=3, turn_count=6, primed=True)
        b = plan_scroll_jump(kind="grok", status="idle", to_index=3, turn_count=6, primed=True)
        self.assertEqual(a.keys, b.keys)

    def test_clamp_out_of_range_index(self) -> None:
        plan = plan_scroll_jump(
            kind="grok",
            status="idle",
            to_index=99,
            turn_count=3,
            primed=True,
        )
        self.assertEqual(plan.keys.count("shift+left"), 0)
        plan = plan_scroll_jump(
            kind="grok",
            status="idle",
            to_index=-2,
            turn_count=3,
            primed=True,
        )
        self.assertEqual(plan.keys.count("shift+left"), 2)

    def test_agy_cannot_jump(self) -> None:
        plan = plan_scroll_jump(
            kind="agy",
            status="idle",
            to_index=0,
            turn_count=4,
            primed=False,
        )
        self.assertTrue(plan.skip_reason)
        self.assertEqual(plan.keys, ())

    def test_empty_turns_cannot_jump(self) -> None:
        plan = plan_scroll_jump(
            kind="grok",
            status="idle",
            to_index=0,
            turn_count=0,
            primed=False,
        )
        self.assertEqual(plan.skip_reason, "no turns")
        self.assertEqual(plan.keys, ())

    def test_claude_uses_the_same_keys(self) -> None:
        plan = plan_scroll_jump(
            kind="claude-code",
            status="idle",
            to_index=1,
            turn_count=4,
            primed=True,
        )
        self.assertEqual(plan.skip_reason, "")
        self.assertEqual(plan.keys.count("shift+right"), 4)
        self.assertEqual(plan.keys.count("shift+left"), 2)


class ClickIndexTests(unittest.TestCase):
    def test_click_maps_to_filtered_row(self) -> None:
        self.assertEqual(click_index(2, body_top=0, body_h=10, scroll=0, count=6), 2)

    def test_click_accounts_for_scroll(self) -> None:
        self.assertEqual(click_index(0, body_top=0, body_h=4, scroll=5, count=12), 5)

    def test_click_ignores_footer_and_header(self) -> None:
        self.assertIsNone(click_index(0, body_top=1, body_h=8, scroll=0, count=6))
        self.assertIsNone(click_index(12, body_top=0, body_h=10, scroll=0, count=6))

    def test_click_ignores_empty_list(self) -> None:
        self.assertIsNone(click_index(0, body_top=0, body_h=10, scroll=0, count=0))


class LayoutTests(unittest.TestCase):
    def test_narrow_pane_keeps_titles(self) -> None:
        geo = layout_for(20, 16, False)
        self.assertGreater(geo.list_width, 8)
        self.assertFalse(geo.show_card)
        label = row_label("当前这个历史栏怎么关掉呢？", False, False, geo.list_width)
        self.assertIn("历史", label)

    def test_short_pane_drops_footer_not_rows(self) -> None:
        geo = layout_for(2, 24, False)
        self.assertEqual(geo.body_h, 2)
        self.assertFalse(geo.show_footer)

    def test_wide_pane_keeps_hover_card(self) -> None:
        geo = layout_for(30, 80, False)
        self.assertTrue(geo.show_card)
        self.assertLessEqual(geo.list_width, 26)


class BindingTests(unittest.TestCase):
    def test_roundtrip(self) -> None:
        text = format_binding("w1:p13", "w1:p1")
        self.assertEqual(parse_binding(text), ("w1:p13", "w1:p1"))

    def test_missing_tab_does_not_glue_ids(self) -> None:
        hist, bound = parse_binding("w1:p13w1:p1")
        self.assertEqual(hist, "w1:p13w1:p1")
        self.assertEqual(bound, "")


if __name__ == "__main__":
    unittest.main()
