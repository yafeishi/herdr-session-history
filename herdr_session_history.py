#!/usr/bin/env python3
"""Per-pane conversation history rail for Herdr."""

from __future__ import annotations

import curses
import json
import locale
import os
import re
import subprocess
import sys
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

HOME = Path.home()
USER_QUERY_RE = re.compile(r"<user_query>\s*(.*?)\s*</user_query>", re.S)


@dataclass
class Turn:
    index: int
    title: str
    preview: str
    updated_at: float = 0.0
    current: bool = False


def herdr_bin() -> str:
    return os.environ.get("HERDR_BIN_PATH") or "herdr"


def herdr(*args: str) -> Any:
    proc = subprocess.run(
        [herdr_bin(), *args],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(err or f"herdr {' '.join(args)} failed ({proc.returncode})")
    text = proc.stdout.strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}
    if isinstance(data, dict) and "result" in data:
        return data["result"]
    return data


def herdr_error_code(message: str) -> str:
    text = (message or "").strip()
    if not text:
        return ""
    blob = text
    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            blob = text[start : end + 1]
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        lowered = text.lower()
        if "pane_not_found" in lowered or ("pane" in lowered and "not found" in lowered):
            return "pane_not_found"
        return ""
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict):
            return str(err.get("code") or "")
    return ""


def pane_is_gone(error_code: str) -> bool:
    return error_code == "pane_not_found"


def target_still_open(pane_id: str) -> bool:
    """False only when Herdr reports the bound pane is gone. Other errors keep the rail."""
    if not pane_id:
        return False
    try:
        result = herdr("pane", "get", pane_id)
    except RuntimeError as exc:
        return not pane_is_gone(herdr_error_code(str(exc)))
    if isinstance(result, dict):
        pane = result.get("pane") or result
        if isinstance(pane, dict) and (pane.get("pane_id") or pane.get("id") or pane.get("tab_id")):
            return True
    return True


def close_own_pane() -> None:
    me = os.environ.get("HERDR_PANE_ID") or ""
    if not me:
        return
    try:
        herdr("plugin", "pane", "close", me)
    except RuntimeError:
        try:
            herdr("pane", "close", me)
        except RuntimeError:
            pass


def context_json() -> dict[str, Any]:
    raw = os.environ.get("HERDR_PLUGIN_CONTEXT_JSON") or "{}"
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def pick_id(obj: Any, *keys: str) -> str:
    if not isinstance(obj, dict):
        return ""
    for key in keys:
        value = obj.get(key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, dict):
            nested = pick_id(value, "id", "pane_id", "workspace_id", "tab_id")
            if nested:
                return nested
    return ""


def sibling_agent_pane(history_pane: str) -> str:
    if not history_pane:
        return ""
    layout = layout_info(history_pane)
    panes = layout.get("panes") if isinstance(layout, dict) else None
    if not isinstance(panes, list):
        return ""
    for item in panes:
        pane_id = str(item.get("pane_id") or "")
        if pane_id and pane_id != history_pane:
            record = pane_record(pane_id)
            if record.get("agent"):
                return pane_id
    return ""


def target_pane_file() -> Path | None:
    root = os.environ.get("HERDR_PLUGIN_STATE_DIR")
    if not root:
        return None
    return Path(root) / "target_pane"


def invoker_pane() -> str:
    env = os.environ.get("HERDR_SESSION_HISTORY_TARGET") or ""
    if env:
        return env
    ctx = context_json()
    for key in ("focused_pane_id", "pane_id"):
        value = ctx.get(key)
        if isinstance(value, str) and value:
            record = pane_record(value)
            if record.get("agent"):
                return value
    stored = target_pane_file()
    if stored and stored.is_file():
        value = stored.read_text().strip()
        if value:
            return value
    me = os.environ.get("HERDR_PANE_ID") or ""
    sibling = sibling_agent_pane(me)
    if sibling:
        return sibling
    pane = ctx.get("pane") or ctx.get("focused_pane") or {}
    return pick_id(pane, "pane_id", "id")


def workspace_cwd() -> str:
    env = os.environ.get("HERDR_SESSION_HISTORY_CWD") or ""
    if env:
        return str(Path(env).expanduser())
    ctx = context_json()
    for key in ("workspace_cwd", "focused_pane_cwd"):
        value = ctx.get(key)
        if isinstance(value, str) and value:
            return str(Path(value).expanduser())
    ws = ctx.get("workspace") or {}
    for key in ("identity_cwd", "cwd", "path"):
        if isinstance(ws, dict) and isinstance(ws.get(key), str) and ws[key]:
            return str(Path(ws[key]).expanduser())
    pane = ctx.get("pane") or ctx.get("focused_pane") or {}
    if isinstance(pane, dict):
        for key in ("cwd", "foreground_cwd", "identity_cwd"):
            if isinstance(pane.get(key), str) and pane[key]:
                return str(Path(pane[key]).expanduser())
    return os.getcwd()


def display_width(text: str) -> int:
    width = 0
    for char in text:
        width += 2 if unicodedata.east_asian_width(char) in ("F", "W") else 1
    return width


def clip(text: str, width: int) -> str:
    if width <= 0:
        return ""
    text = " ".join(text.split())
    if display_width(text) <= width:
        return text
    out: list[str] = []
    used = 0
    for char in text:
        size = 2 if unicodedata.east_asian_width(char) in ("F", "W") else 1
        if used + size >= width:
            break
        out.append(char)
        used += size
    return "".join(out)


def pad(text: str, width: int) -> str:
    text = clip(text, width)
    extra = width - display_width(text)
    return text + (" " * max(0, extra))


@dataclass
class Layout:
    body_top: int
    body_h: int
    list_width: int
    show_footer: bool
    show_card: bool
    card_x: int


def layout_for(height: int, width: int, searching: bool) -> Layout:
    """Fit the rail into a compressed pane: keep titles, drop chrome first."""
    height = max(0, int(height))
    width = max(0, int(width))
    body_top = 1 if searching and height >= 2 else 0
    show_footer = (height - body_top) >= 3
    body_h = max(0, height - body_top - (1 if show_footer else 0))
    usable = max(0, width - 1)
    if usable >= 40:
        list_width = min(26, max(16, usable // 3))
        show_card = (usable - list_width - 1) >= 18
        if not show_card:
            list_width = usable
    else:
        list_width = usable
        show_card = False
    return Layout(
        body_top=body_top,
        body_h=body_h,
        list_width=list_width,
        show_footer=show_footer,
        show_card=show_card,
        card_x=list_width + 1,
    )


def click_index(y: int, body_top: int, body_h: int, scroll: int, count: int) -> int | None:
    """Map a click row to a filtered-turn index. Footer / header clicks are ignored."""
    if count <= 0 or body_h <= 0:
        return None
    if y < body_top or y >= body_top + body_h:
        return None
    index = scroll + (y - body_top)
    if 0 <= index < count:
        return index
    return None


def parse_binding(text: str) -> tuple[str, str]:
    raw = (text or "").strip()
    hist, sep, bound = raw.partition("\t")
    if sep:
        return hist.strip(), bound.strip()
    return raw, ""


def format_binding(history_pane: str, target_pane: str) -> str:
    return f"{history_pane}\t{target_pane}"


def should_reuse_history(existing: str, bound: str, pane: str) -> bool:
    """Reuse the open rail only when it is already bound to this pane."""
    return bool(existing and pane and bound == pane)


def should_swap_to_left(history_x: int, conversation_x: int) -> bool:
    """True when the history split landed to the right of the conversation."""
    return history_x > conversation_x


def row_label(title: str, selected: bool, current: bool, width: int) -> str:
    title = title or "(untitled)"
    if width <= 0:
        return ""
    if width >= 18:
        if selected:
            mark = "━━━━" if current else "━━━"
        else:
            mark = "  ● " if current else "  ─ "
        return pad(f"{mark} {title}", width)
    mark = ("━" if current else "─") if selected else ("●" if current else "·")
    if width <= 2:
        return pad(mark, width)
    return pad(f"{mark} {title}", width)


def sync_curses_size(stdscr: curses.window) -> tuple[int, int]:
    """Follow the pane pty. Do not force resizeterm — that desyncs curses in Herdr splits."""
    try:
        curses.update_lines_cols()
    except Exception:
        pass
    try:
        height, width = stdscr.getmaxyx()
    except curses.error:
        height, width = 0, 0
    return max(0, height), max(0, width)


def content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
            elif isinstance(item, dict) and isinstance(item.get("input_text"), str):
                parts.append(item["input_text"])
        return "\n".join(parts)
    if isinstance(content, dict):
        return content_text(content.get("content") or content.get("text") or "")
    return ""


def grok_chat_path(cwd: str, session_id: str) -> Path:
    return HOME / ".grok" / "sessions" / quote(cwd, safe="") / session_id / "chat_history.jsonl"


def claude_chat_path(cwd: str, session_id: str) -> Path:
    encoded = cwd.replace("/", "-")
    if not encoded.startswith("-"):
        encoded = "-" + encoded
    return HOME / ".claude" / "projects" / encoded / f"{session_id}.jsonl"


def turns_from_grok(path: Path) -> list[Turn]:
    turns: list[Turn] = []
    replies: list[str] = []
    mtime = path.stat().st_mtime

    def flush_reply() -> None:
        if turns and replies:
            body = turns[-1].preview
            reply = "\n".join(replies).strip()
            turns[-1].preview = (body + "\n\n" + reply).strip() if body else reply
        replies.clear()

    with path.open() as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = obj.get("type")
            if kind == "user":
                text = content_text(obj.get("content"))
                match = USER_QUERY_RE.search(text)
                if not match:
                    continue
                flush_reply()
                query = match.group(1).strip()
                title = query.splitlines()[0][:80] if query else "(untitled)"
                turns.append(Turn(index=len(turns), title=title, preview=query, updated_at=mtime))
            elif kind == "assistant":
                text = content_text(obj.get("content")).strip()
                if text:
                    replies.append(text)
    flush_reply()
    if turns:
        turns[-1].current = True
    return turns


def turns_from_claude(path: Path) -> list[Turn]:
    turns: list[Turn] = []
    replies: list[str] = []
    mtime = path.stat().st_mtime

    def flush_reply() -> None:
        if turns and replies:
            turns[-1].preview = (turns[-1].preview + "\n\n" + "\n".join(replies)).strip()
        replies.clear()

    with path.open() as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = obj.get("type")
            if kind == "user":
                text = first_user_text(obj.get("message") or obj)
                if not text or text.lstrip().startswith("<"):
                    continue
                flush_reply()
                title = text.splitlines()[0][:80]
                turns.append(Turn(index=len(turns), title=title, preview=text, updated_at=mtime))
            elif kind == "assistant":
                text = first_user_text(obj.get("message") or obj)
                if text:
                    replies.append(text)
    flush_reply()
    if turns:
        turns[-1].current = True
    return turns


def first_user_text(message: Any) -> str:
    if isinstance(message, str):
        return message.strip()
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                for key in ("text", "input_text", "content"):
                    if isinstance(item.get(key), str) and item[key].strip():
                        parts.append(item[key])
                        break
        return "\n".join(parts).strip()
    return ""


def load_turns_for_pane(pane_id: str) -> tuple[list[Turn], float]:
    """Turns of the conversation currently in pane_id. Independent per pane."""
    record = pane_record(pane_id)
    kind = str(record.get("agent") or "").lower()
    if kind in ("antigravity", "antigravity-cli"):
        kind = "agy"
    session_id = pane_session_id(pane_id)
    cwd = str(record.get("cwd") or record.get("foreground_cwd") or workspace_cwd() or "")
    if not session_id:
        return [], 0.0
    if kind == "grok":
        path = grok_chat_path(cwd, session_id)
        if path.is_file():
            return turns_from_grok(path), path.stat().st_mtime
    if kind in ("claude", "claude-code"):
        path = claude_chat_path(cwd, session_id)
        if path.is_file():
            return turns_from_claude(path), path.stat().st_mtime
        # encoded project dirs may not match cwd exactly
        root = HOME / ".claude" / "projects"
        if root.is_dir():
            match = next(root.glob(f"*/{session_id}.jsonl"), None)
            if match:
                return turns_from_claude(match), match.stat().st_mtime
    return [], 0.0


def pane_record(pane_id: str) -> dict[str, Any]:
    try:
        result = herdr("pane", "get", pane_id)
    except RuntimeError:
        return {}
    if isinstance(result, dict):
        pane = result.get("pane") or result
        return pane if isinstance(pane, dict) else {}
    return {}


def pane_tab(pane_id: str) -> str:
    record = pane_record(pane_id)
    tab = str(record.get("tab_id") or "")
    if tab:
        return tab
    return ""


def pane_session_id(pane_id: str) -> str:
    record = pane_record(pane_id)
    session = record.get("agent_session") if isinstance(record.get("agent_session"), dict) else {}
    return str(session.get("value") or "")


JUMP_KEY_PAUSE = 0.02


def send_keys(pane_id: str, *keys: str) -> None:
    if not keys:
        return
    try:
        herdr("agent", "send-keys", pane_id, *keys)
    except RuntimeError:
        herdr("pane", "send-keys", pane_id, *keys)


def send_keys_paced(pane_id: str, keys: list[str] | tuple[str, ...], pause: float | None = None) -> None:
    delay = JUMP_KEY_PAUSE if pause is None else pause
    for i, key in enumerate(keys):
        send_keys(pane_id, key)
        if delay and i + 1 < len(keys):
            time.sleep(delay)


@dataclass(frozen=True)
class ScrollJump:
    keys: tuple[str, ...]
    primed: bool
    skip_reason: str = ""


def plan_scroll_jump(
    *,
    kind: str,
    status: str,
    to_index: int,
    turn_count: int,
    primed: bool,
) -> ScrollJump:
    """Absolute turn jump. Relative Shift+Left/Right desyncs after focus/scroll."""
    kind = str(kind or "").lower()
    if kind in ("antigravity", "antigravity-cli"):
        kind = "agy"
    if kind not in ("grok", "claude", "claude-code", "codex"):
        return ScrollJump((), primed, f"no scroll jump for {kind or 'this agent'}")
    if turn_count <= 0:
        return ScrollJump((), primed, "no turns")
    to_index = max(0, min(int(to_index), turn_count - 1))
    keys: list[str] = []
    next_primed = True
    # Working Grok already parks the keyboard in scrollback. Tab would leave it.
    if status != "working" and not primed:
        keys.append("tab")
    keys.extend(["shift+right"] * turn_count)
    keys.extend(["shift+left"] * ((turn_count - 1) - to_index))
    return ScrollJump(tuple(keys), next_primed)


def jump_scrollback(pane_id: str, to_index: int, turn_count: int, primed: bool) -> bool:
    """Move Grok/Claude scrollback to a user-turn. Returns primed."""
    record = pane_record(pane_id)
    plan = plan_scroll_jump(
        kind=str(record.get("agent") or ""),
        status=str(record.get("agent_status") or ""),
        to_index=to_index,
        turn_count=turn_count,
        primed=primed,
    )
    if plan.skip_reason:
        raise RuntimeError(plan.skip_reason)
    send_keys_paced(pane_id, plan.keys)
    return plan.primed


def layout_info(pane_id: str) -> dict[str, Any]:
    try:
        result = herdr("pane", "layout", "--pane", pane_id)
    except RuntimeError:
        return {}
    if isinstance(result, dict):
        return result.get("layout") or result
    return {}


def dock_as_left_rail(history_pane: str, conversation_pane: str) -> None:
    """Put the history pane on the left of the conversation, then shrink it."""
    if not history_pane or not conversation_pane or history_pane == conversation_pane:
        return
    layout = layout_info(history_pane)
    panes = layout.get("panes") if isinstance(layout, dict) else None
    if not isinstance(panes, list):
        return
    mine = next((item for item in panes if item.get("pane_id") == history_pane), None)
    other = next((item for item in panes if item.get("pane_id") == conversation_pane), None)
    if not isinstance(mine, dict) or not isinstance(other, dict):
        return
    my_x = int((mine.get("rect") or {}).get("x") or 0)
    other_x = int((other.get("rect") or {}).get("x") or 0)
    if should_swap_to_left(my_x, other_x):
        try:
            herdr("pane", "swap", "--source-pane", history_pane, "--target-pane", conversation_pane)
        except RuntimeError:
            return
    # Leave width alone. The TUI draws a thin tick rail + hover card inside
    # whatever column the user keeps; auto-resize fights nested splits.


# --- TUI ----------------------------------------------------------------------


def safe_add(stdscr: curses.window, y: int, x: int, text: str, attr: int = 0) -> None:
    try:
        height, width = stdscr.getmaxyx()
    except curses.error:
        return
    if height <= 0 or width <= 0 or y < 0 or y >= height or x >= width or x < 0:
        return
    # Never write the last column: ncurses errors on the bottom-right cell,
    # and a full-width addstr is dropped entirely in a Herdr split.
    limit = max(0, width - x - 1)
    clipped = clip(text, limit)
    try:
        stdscr.addstr(y, x, clipped, attr)
        return
    except curses.error:
        pass
    try:
        fallback = clipped.encode("ascii", "replace").decode("ascii")
        stdscr.addstr(y, x, fallback, attr)
    except curses.error:
        pass


class HistoryTUI:
    def __init__(self, stdscr: curses.window) -> None:
        self.stdscr = stdscr
        self.target = invoker_pane()
        self.query = ""
        self.searching = False
        self.cursor = 0
        self.scroll = 0
        self.status = ""
        self.body_top = 1
        self.source_mtime = 0.0
        self.turns: list[Turn] = []
        self.filtered: list[Turn] = []
        self.viewed_index = 0
        self.scrollback_primed = False
        self.reload(follow_latest=True)
        if self.filtered:
            self.viewed_index = self.filtered[-1].index

    def reload(self, follow_latest: bool = False) -> None:
        self.turns, self.source_mtime = load_turns_for_pane(self.target)
        previous_last = follow_latest or (self.filtered and self.cursor == len(self.filtered) - 1)
        self.apply_filter()
        if previous_last and self.filtered:
            self.cursor = len(self.filtered) - 1
        self.status = ""

    def apply_filter(self) -> None:
        items = self.turns
        needle = self.query.strip().lower()
        if needle:
            items = [
                item
                for item in items
                if needle in item.title.lower() or needle in item.preview.lower()
            ]
        self.filtered = items
        if self.cursor >= len(self.filtered):
            self.cursor = max(0, len(self.filtered) - 1)
        if self.cursor < 0:
            self.cursor = 0

    def current(self) -> Turn | None:
        if not self.filtered:
            return None
        return self.filtered[self.cursor]

    def row_geometry(self) -> Layout:
        height, width = sync_curses_size(self.stdscr)
        return layout_for(height, width, self.searching)

    def draw(self) -> None:
        stdscr = self.stdscr
        height, width = sync_curses_size(stdscr)
        if height <= 0 or width <= 0:
            return
        try:
            stdscr.erase()
        except curses.error:
            return
        geo = layout_for(height, width, self.searching)
        self.body_top = geo.body_top
        if self.searching:
            safe_add(stdscr, 0, 0, f"/{self.query}▌", curses.A_DIM)

        if geo.body_h <= 0:
            turn = self.current()
            if turn:
                safe_add(
                    stdscr,
                    0,
                    0,
                    row_label(turn.title, True, turn.current, width),
                    curses.A_BOLD,
                )
            try:
                stdscr.refresh()
            except curses.error:
                pass
            return

        if self.cursor < self.scroll:
            self.scroll = self.cursor
        if self.cursor >= self.scroll + geo.body_h:
            self.scroll = self.cursor - geo.body_h + 1

        visible = self.filtered[self.scroll : self.scroll + geo.body_h]
        selected_row = None
        if not visible:
            safe_add(stdscr, geo.body_top, 0, "no turns in this pane", curses.A_DIM)
        for offset, turn in enumerate(visible):
            row = geo.body_top + offset
            selected = (self.scroll + offset) == self.cursor
            if selected:
                selected_row = row
            attr = curses.A_BOLD if selected else curses.A_DIM
            safe_add(
                stdscr,
                row,
                0,
                row_label(turn.title, selected, turn.current, geo.list_width),
                attr,
            )

        turn = self.current()
        if geo.show_card and turn and selected_row is not None:
            card_bottom = height - (1 if geo.show_footer else 0)
            self.draw_card(turn, selected_row, geo.card_x, width - geo.card_x, card_bottom)

        if geo.show_footer:
            footer = self.status or "↑↓ jump in chat  / search  q"
            safe_add(stdscr, height - 1, 0, footer, curses.A_DIM)
        try:
            stdscr.refresh()
        except curses.error:
            pass

    def draw_card(
        self,
        turn: Turn,
        row: int,
        x: int,
        max_width: int,
        max_bottom: int,
    ) -> None:
        inner_w = min(42, max(16, max_width - 2))
        snippets = self.card_snippets(turn, inner_w)
        lines = [clip(turn.title or "(untitled)", inner_w)] + snippets[:4]
        box_h = len(lines) + 2
        top = row - 1
        if top < 0:
            top = 0
        if top + box_h > max_bottom:
            top = max(0, max_bottom - box_h)
        attr = curses.A_DIM
        safe_add(self.stdscr, top, x, "╭" + ("─" * inner_w) + "╮", attr)
        for index, line in enumerate(lines):
            if index == 0:
                content = pad(line, inner_w)
                line_attr = curses.A_BOLD
            else:
                bar_w = max(8, inner_w - (index % 3) * 6)
                content = pad(clip(line, bar_w), inner_w)
                line_attr = curses.A_DIM
            safe_add(self.stdscr, top + 1 + index, x, "│" + content + "│", line_attr)
        safe_add(self.stdscr, top + box_h - 1, x, "╰" + ("─" * inner_w) + "╯", attr)
        meta = f"#{turn.index + 1}/{len(self.turns)}"
        if turn.current:
            meta += "  now"
        if top + box_h < max_bottom:
            safe_add(self.stdscr, top + box_h, x + 1, meta, curses.A_DIM)

    def card_snippets(self, turn: Turn, width: int) -> list[str]:
        parts: list[str] = []
        for raw in (turn.preview or "").splitlines():
            text = " ".join(raw.split())
            if not text:
                continue
            parts.append(clip(text, width))
            if len(parts) >= 4:
                break
        if not parts:
            parts = ["(empty)"]
        return parts

    def index_at(self, y: int) -> int | None:
        geo = self.row_geometry()
        return click_index(y, geo.body_top, geo.body_h, self.scroll, len(self.filtered))

    def jump_here(self) -> None:
        turn = self.current()
        if turn is None:
            return
        try:
            self.scrollback_primed = jump_scrollback(
                self.target,
                turn.index,
                len(self.turns),
                self.scrollback_primed,
            )
            self.viewed_index = turn.index
            self.status = f"#{turn.index + 1}/{len(self.turns)}"
        except Exception as exc:  # noqa: BLE001
            self.status = str(exc).splitlines()[0][:80]

    def run(self) -> None:
        curses.curs_set(0)
        curses.use_default_colors()
        self.stdscr.timeout(700)
        self.stdscr.keypad(True)
        curses.mousemask(curses.BUTTON1_CLICKED | curses.BUTTON1_PRESSED)
        while True:
            try:
                self.draw()
                key = self.stdscr.getch()
            except curses.error:
                time.sleep(0.05)
                continue
            if key == -1:
                if not target_still_open(self.target):
                    close_own_pane()
                    return
                _turns, mtime = load_turns_for_pane(self.target)
                if mtime != self.source_mtime:
                    self.reload()
                continue
            if self.searching:
                if key in (27,):
                    self.searching = False
                    self.query = ""
                    self.apply_filter()
                elif key in (curses.KEY_BACKSPACE, 127, 8):
                    self.query = self.query[:-1]
                    self.apply_filter()
                elif key in (10, 13):
                    self.searching = False
                elif 32 <= key <= 126:
                    self.query += chr(key)
                    self.apply_filter()
                continue
            if key in (ord("q"), 27):
                return
            if key in (curses.KEY_UP, ord("k")):
                self.cursor = max(0, self.cursor - 1)
                self.jump_here()
            elif key in (curses.KEY_DOWN, ord("j")):
                self.cursor = min(max(0, len(self.filtered) - 1), self.cursor + 1)
                self.jump_here()
            elif key == curses.KEY_PPAGE:
                self.cursor = max(0, self.cursor - 10)
                self.jump_here()
            elif key == curses.KEY_NPAGE:
                self.cursor = min(max(0, len(self.filtered) - 1), self.cursor + 10)
                self.jump_here()
            elif key in (10, 13):
                self.jump_here()
            elif key == ord("/"):
                self.searching = True
                self.query = ""
            elif key == ord("r"):
                self.reload()
            elif key == curses.KEY_MOUSE:
                try:
                    _, _x, y, _, _bstate = curses.getmouse()
                except curses.error:
                    continue
                index = self.index_at(y)
                if index is not None:
                    self.cursor = index
                    self.jump_here()
            elif key == curses.KEY_RESIZE:
                try:
                    self.stdscr.erase()
                    self.stdscr.redrawwin()
                except curses.error:
                    pass
                continue


def cmd_list(_show_all: bool) -> int:
    pane = invoker_pane()
    turns, _mtime = load_turns_for_pane(pane)
    payload = [{"index": item.index, "title": item.title, "current": item.current} for item in turns]
    json.dump({"pane": pane, "count": len(turns), "turns": payload}, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


def state_pane_file() -> Path | None:
    root = os.environ.get("HERDR_PLUGIN_STATE_DIR")
    if not root:
        return None
    return Path(root) / "history_pane_id"


def remember_pane() -> None:
    path = state_pane_file()
    pane = os.environ.get("HERDR_PANE_ID") or ""
    target = os.environ.get("HERDR_SESSION_HISTORY_TARGET") or ""
    if path and pane:
        path.write_text(format_binding(pane, target))


def cmd_open() -> int:
    pane = invoker_pane()
    pending = target_pane_file()
    if pending and pane:
        pending.write_text(pane)
    cwd = workspace_cwd()
    env_args: list[str] = []
    if pane:
        env_args.extend(["--env", f"HERDR_SESSION_HISTORY_TARGET={pane}"])
    if cwd:
        env_args.extend(["--env", f"HERDR_SESSION_HISTORY_CWD={cwd}"])
    stored = state_pane_file()
    if stored and stored.is_file():
        existing, bound = parse_binding(stored.read_text())
        if should_reuse_history(existing, bound, pane):
            try:
                herdr("plugin", "pane", "focus", existing)
                return 0
            except RuntimeError:
                stored.unlink(missing_ok=True)
    opened = herdr(
        "plugin",
        "pane",
        "open",
        "--plugin",
        "herdr-session-history",
        "--entrypoint",
        "history",
        "--placement",
        "split",
        "--direction",
        "right",
        *(["--target-pane", pane] if pane else []),
        "--focus",
        *env_args,
    )
    hist = ""
    if isinstance(opened, dict):
        hist = pick_id((opened.get("plugin_pane") or {}).get("pane") or {}, "pane_id", "id")
    if hist:
        remember = state_pane_file()
        if remember:
            remember.write_text(format_binding(hist, pane))
        dock_as_left_rail(hist, pane)
    return 0


def cmd_tui() -> int:
    remember_pane()
    try:
        locale.setlocale(locale.LC_ALL, "")
    except locale.Error:
        pass
    try:
        curses.wrapper(lambda stdscr: HistoryTUI(stdscr).run())
    except (KeyboardInterrupt, curses.error):
        return 0
    finally:
        stored = state_pane_file()
        if stored and stored.is_file():
            stored.unlink(missing_ok=True)
    return 0


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "tui"
    if cmd == "list":
        return cmd_list("--all" in argv)
    if cmd == "open":
        return cmd_open()
    if cmd == "tui":
        return cmd_tui()
    sys.stderr.write("usage: herdr_session_history.py tui|open|list [--all]\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
