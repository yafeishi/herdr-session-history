#!/usr/bin/env python3
"""Parse Grok/Claude transcripts and keep panes independent."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import herdr_session_history as h  # noqa: E402


def write_jsonl(path: Path, rows: list[dict | str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for row in rows:
        if isinstance(row, str):
            lines.append(row)
        else:
            lines.append(json.dumps(row, ensure_ascii=False))
    path.write_text("\n".join(lines) + "\n")
    return path


class GrokTurnsTests(unittest.TestCase):
    def test_extracts_user_query_turns_and_attaches_replies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_jsonl(
                Path(tmp) / "chat_history.jsonl",
                [
                    {"type": "user", "content": "<user_query>hello world</user_query>"},
                    {"type": "assistant", "content": "hi there"},
                    {"type": "user", "content": "not wrapped, ignore"},
                    {"type": "user", "content": "<user_query>second\nline</user_query>"},
                    {"type": "assistant", "content": [{"text": "ok"}]},
                    "not-json",
                    {"type": "user", "content": "<user_query>  当前这个历史栏怎么关掉呢？  </user_query>"},
                ],
            )
            turns = h.turns_from_grok(path)
            self.assertEqual([t.title for t in turns], ["hello world", "second", "当前这个历史栏怎么关掉呢？"])
            self.assertEqual(turns[0].preview.splitlines()[0], "hello world")
            self.assertIn("hi there", turns[0].preview)
            self.assertTrue(turns[-1].current)
            self.assertFalse(turns[0].current)
            self.assertEqual([t.index for t in turns], [0, 1, 2])

    def test_empty_and_tool_only_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            empty = write_jsonl(Path(tmp) / "empty.jsonl", [])
            self.assertEqual(h.turns_from_grok(empty), [])
            tools = write_jsonl(
                Path(tmp) / "tools.jsonl",
                [{"type": "user", "content": [{"type": "tool_result", "text": "ok"}]}],
            )
            self.assertEqual(h.turns_from_grok(tools), [])


class ClaudeTurnsTests(unittest.TestCase):
    def test_reads_message_content_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_jsonl(
                Path(tmp) / "session.jsonl",
                [
                    {"type": "user", "message": {"content": [{"type": "text", "text": "Please review"}]}},
                    {"type": "assistant", "message": {"content": [{"text": "looks good"}]}},
                    {"type": "user", "message": {"content": "<tool_result>ignored</tool_result>"}},
                    {"type": "user", "message": {"content": "follow up"}},
                ],
            )
            turns = h.turns_from_claude(path)
            self.assertEqual([t.title for t in turns], ["Please review", "follow up"])
            self.assertIn("looks good", turns[0].preview)
            self.assertTrue(turns[-1].current)

    def test_claude_path_uses_leading_dash_project_dir(self) -> None:
        path = h.claude_chat_path("/Users/dang/AICODING", "abc")
        self.assertEqual(path.name, "abc.jsonl")
        self.assertEqual(path.parent.name, "-Users-dang-AICODING")


class ContentTextTests(unittest.TestCase):
    def test_nested_shapes(self) -> None:
        self.assertEqual(h.content_text("plain"), "plain")
        self.assertEqual(h.content_text([{"text": "a"}, {"input_text": "b"}]), "a\nb")
        self.assertEqual(h.content_text({"content": {"text": "inner"}}), "inner")
        self.assertEqual(h.first_user_text({"content": [{"text": " hi "}]}), "hi")


class PerPaneLoadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.orig_home = h.HOME
        h.HOME = self.home
        self.addCleanup(lambda: setattr(h, "HOME", self.orig_home))

    def _put_grok(self, cwd: str, session: str, query: str) -> None:
        path = self.home / ".grok" / "sessions" / quote(cwd, safe="") / session / "chat_history.jsonl"
        write_jsonl(path, [{"type": "user", "content": f"<user_query>{query}</user_query>"}])

    def test_two_panes_do_not_share_turns(self) -> None:
        left_cwd = "/work/left"
        right_cwd = "/work/right"
        self._put_grok(left_cwd, "sess-left", "left only")
        self._put_grok(right_cwd, "sess-right", "right only")
        records = {
            "w1:p1": {
                "agent": "grok",
                "agent_session": {"value": "sess-left"},
                "cwd": left_cwd,
            },
            "w1:p11": {
                "agent": "grok",
                "agent_session": {"value": "sess-right"},
                "cwd": right_cwd,
            },
        }
        orig = h.pane_record
        h.pane_record = lambda pid: records.get(pid, {})  # type: ignore[assignment]
        self.addCleanup(lambda: setattr(h, "pane_record", orig))

        left, _ = h.load_turns_for_pane("w1:p1")
        right, _ = h.load_turns_for_pane("w1:p11")
        self.assertEqual([t.title for t in left], ["left only"])
        self.assertEqual([t.title for t in right], ["right only"])

    def test_missing_session_is_empty_not_global(self) -> None:
        orig = h.pane_record
        h.pane_record = lambda pid: {"agent": "grok", "cwd": "/x"}  # type: ignore[assignment]
        self.addCleanup(lambda: setattr(h, "pane_record", orig))
        turns, mtime = h.load_turns_for_pane("w1:p9")
        self.assertEqual(turns, [])
        self.assertEqual(mtime, 0.0)

    def test_codex_has_no_turn_list_yet(self) -> None:
        orig = h.pane_record
        h.pane_record = lambda pid: {  # type: ignore[assignment]
            "agent": "codex",
            "agent_session": {"value": "r1"},
            "cwd": "/x",
        }
        self.addCleanup(lambda: setattr(h, "pane_record", orig))
        turns, _ = h.load_turns_for_pane("w1:p3")
        self.assertEqual(turns, [])


class PathTests(unittest.TestCase):
    def test_grok_path_urlencodes_cwd(self) -> None:
        path = h.grok_chat_path("/Users/dang/AICODING", "sid")
        self.assertIn(quote("/Users/dang/AICODING", safe=""), str(path))
        self.assertTrue(str(path).endswith("/sid/chat_history.jsonl"))


if __name__ == "__main__":
    unittest.main()
