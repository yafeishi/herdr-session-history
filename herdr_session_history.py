#!/usr/bin/env python3
"""Codex-style session history for Herdr: list, preview, resume."""

from __future__ import annotations

import curses
import json
import os
import sqlite3
import subprocess
import sys
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

HOME = Path.home()
PROVIDERS = ("grok", "claude", "codex", "agy")
LABEL = {"grok": "grok", "claude": "clau", "codex": "cdx", "agy": "agy"}
MAX_CODEX = 180
MAX_CLAUDE = 180
MAX_AGY = 180


@dataclass
class Session:
    provider: str
    session_id: str
    title: str
    preview: str
    cwd: str
    updated_at: float
    live_pane: str | None = None


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


def invoker_pane() -> str:
    env = os.environ.get("HERDR_SESSION_HISTORY_TARGET") or ""
    if env:
        return env
    ctx = context_json()
    for key in ("focused_pane_id", "pane_id"):
        value = ctx.get(key)
        if isinstance(value, str) and value:
            return value
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


def rel_time(ts: float, now: float | None = None) -> str:
    now = now or time.time()
    if ts <= 0:
        return "?"
    delta = max(0, int(now - ts))
    if delta < 60:
        return "now"
    if delta < 3600:
        return f"{delta // 60}m"
    if delta < 86400:
        return f"{delta // 3600}h"
    if delta < 86400 * 7:
        return f"{delta // 86400}d"
    local = datetime.fromtimestamp(ts).strftime("%m-%d")
    return local


def parse_time(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return 0.0
    if text.isdigit():
        n = int(text)
        return n / 1000 if n > 10_000_000_000 else float(n)
    text = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return 0.0


def file_uri_path(uri: str) -> str:
    uri = uri.strip()
    if uri.startswith("file://"):
        parsed = urlparse(uri)
        return unquote(parsed.path)
    return unquote(uri)


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


def looks_like_meta(text: str) -> bool:
    stripped = text.lstrip()
    return stripped.startswith("<") or stripped.startswith("# ") and "policy" in stripped.lower()


# --- indexers -----------------------------------------------------------------


def index_grok() -> list[Session]:
    db = HOME / ".grok" / "sessions" / "session_search.sqlite"
    if not db.is_file():
        return []
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT session_id, cwd, updated_at, title, content FROM session_docs"
    ).fetchall()
    conn.close()
    sessions: list[Session] = []
    for session_id, cwd, updated_at, title, content in rows:
        preview = (content or "").strip()
        heading = (title or "").strip()
        if not heading and preview:
            heading = preview.splitlines()[0][:80]
        sessions.append(
            Session(
                provider="grok",
                session_id=str(session_id),
                title=heading or "(untitled)",
                preview=preview,
                cwd=str(cwd or ""),
                updated_at=parse_time(updated_at),
            )
        )
    return sessions


def index_claude() -> list[Session]:
    root = HOME / ".claude" / "projects"
    if not root.is_dir():
        return []
    files = sorted(root.glob("*/*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    sessions: list[Session] = []
    for path in files[:MAX_CLAUDE]:
        try:
            session = parse_claude(path)
        except OSError:
            continue
        if session:
            sessions.append(session)
    return sessions


def parse_claude(path: Path) -> Session | None:
    session_id = path.stem
    title = ""
    cwd = ""
    preview_parts: list[str] = []
    updated = path.stat().st_mtime
    try:
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
                if kind == "ai-title" and not title:
                    title = str(obj.get("aiTitle") or obj.get("title") or "").strip()
                if not cwd and isinstance(obj.get("cwd"), str):
                    cwd = obj["cwd"]
                ts = parse_time(obj.get("timestamp"))
                if ts:
                    updated = max(updated, ts)
                if kind == "user":
                    text = first_user_text(obj.get("message"))
                    if text and not looks_like_meta(text):
                        preview_parts.append(text)
                        if not title:
                            title = text.splitlines()[0][:80]
                if len(preview_parts) >= 4:
                    # keep scanning for title/cwd/time
                    if title and cwd:
                        break
    except OSError:
        return None
    if not cwd:
        encoded = path.parent.name
        cwd = "/" + encoded.replace("-", "/").lstrip("/")
    return Session(
        provider="claude",
        session_id=session_id,
        title=title or "(untitled)",
        preview="\n\n".join(preview_parts),
        cwd=cwd,
        updated_at=updated,
    )


def index_codex() -> list[Session]:
    root = HOME / ".codex" / "sessions"
    if not root.is_dir():
        return []
    files = sorted(root.rglob("rollout-*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    sessions: list[Session] = []
    for path in files[:MAX_CODEX]:
        try:
            session = parse_codex(path)
        except OSError:
            continue
        if session:
            sessions.append(session)
    return sessions


def parse_codex(path: Path) -> Session | None:
    session_id = ""
    cwd = ""
    title = ""
    preview_parts: list[str] = []
    updated = path.stat().st_mtime
    try:
        with path.open() as handle:
            for index, line in enumerate(handle):
                if index > 80 and title and cwd:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = parse_time(obj.get("timestamp"))
                if ts:
                    updated = max(updated, ts)
                payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
                kind = obj.get("type")
                if kind == "session_meta":
                    session_id = str(payload.get("session_id") or payload.get("id") or session_id)
                    if isinstance(payload.get("cwd"), str):
                        cwd = payload["cwd"]
                if kind == "turn_context" and isinstance(payload.get("cwd"), str) and not cwd:
                    cwd = payload["cwd"]
                if payload.get("type") == "message" and payload.get("role") == "user":
                    text = first_user_text(payload)
                    if text and not looks_like_meta(text):
                        preview_parts.append(text)
                        if not title:
                            title = text.splitlines()[0][:80]
    except OSError:
        return None
    if not session_id:
        # rollout-...-<uuid>.jsonl
        name = path.stem
        session_id = name.rsplit("-", 5)
        session_id = "-".join(name.split("-")[-5:]) if name.count("-") >= 5 else name
    return Session(
        provider="codex",
        session_id=session_id,
        title=title or "(untitled)",
        preview="\n\n".join(preview_parts),
        cwd=cwd,
        updated_at=updated,
    )


def index_agy() -> list[Session]:
    sessions: dict[str, Session] = {}
    db = HOME / ".gemini" / "antigravity-cli" / "conversation_summaries.db"
    if db.is_file():
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT conversation_id, title, preview, last_modified_time, workspace_uris "
                "FROM conversation_summaries"
            ).fetchall()
        except sqlite3.DatabaseError:
            rows = []
        conn.close()
        for conv_id, title, preview, modified, uris in rows:
            cwd = ""
            try:
                parsed = json.loads(uris or "[]")
                if isinstance(parsed, list) and parsed:
                    cwd = file_uri_path(str(parsed[0]))
            except json.JSONDecodeError:
                cwd = ""
            text = (preview or "").strip()
            sessions[str(conv_id)] = Session(
                provider="agy",
                session_id=str(conv_id),
                title=(title or "").strip() or (text.splitlines()[0][:80] if text else "(untitled)"),
                preview=text,
                cwd=cwd,
                updated_at=parse_time(modified),
            )
    conv_dir = HOME / ".gemini" / "antigravity-cli" / "conversations"
    if conv_dir.is_dir():
        for path in list(conv_dir.glob("*.db"))[:MAX_AGY]:
            conv_id = path.stem
            existing = sessions.get(conv_id)
            mtime = path.stat().st_mtime
            if existing:
                existing.updated_at = max(existing.updated_at, mtime)
            else:
                sessions[conv_id] = Session(
                    provider="agy",
                    session_id=conv_id,
                    title=f"agy {conv_id[:8]}",
                    preview="",
                    cwd="",
                    updated_at=mtime,
                )
    return list(sessions.values())


def live_sessions() -> dict[tuple[str, str], str]:
    mapping: dict[tuple[str, str], str] = {}
    try:
        result = herdr("agent", "list")
    except RuntimeError:
        return mapping
    agents = result.get("agents") if isinstance(result, dict) else None
    if not isinstance(agents, list):
        return mapping
    for agent in agents:
        if not isinstance(agent, dict):
            continue
        kind = str(agent.get("agent") or "").lower()
        if kind in ("antigravity", "antigravity-cli"):
            kind = "agy"
        pane_id = str(agent.get("pane_id") or "")
        session = agent.get("agent_session") if isinstance(agent.get("agent_session"), dict) else {}
        value = str(session.get("value") or "")
        if kind and value and pane_id:
            mapping[(kind, value)] = pane_id
        tokens = agent.get("tokens") if isinstance(agent.get("tokens"), dict) else {}
        topic = str(tokens.get("quota_topic") or "").strip()
        if kind == "agy" and value and topic:
            # fill title later
            mapping[(kind, value + "::topic")] = topic  # type: ignore[assignment]
    return mapping


def attach_live(sessions: list[Session]) -> None:
    live = live_sessions()
    for session in sessions:
        pane = live.get((session.provider, session.session_id))
        if pane:
            session.live_pane = pane
        topic = live.get((session.provider, session.session_id + "::topic"))
        if topic and (not session.title or session.title.startswith("agy ")):
            session.title = str(topic).splitlines()[0][:80]


def load_sessions() -> list[Session]:
    sessions: list[Session] = []
    for loader in (index_grok, index_claude, index_codex, index_agy):
        try:
            sessions.extend(loader())
        except Exception as exc:  # noqa: BLE001 — one provider must not kill the list
            sys.stderr.write(f"{loader.__name__}: {exc}\n")
    attach_live(sessions)
    sessions.sort(key=lambda item: item.updated_at, reverse=True)
    return sessions


def same_cwd(session_cwd: str, current: str) -> bool:
    if not session_cwd or not current:
        return False
    try:
        left = Path(session_cwd).expanduser().resolve()
        right = Path(current).expanduser().resolve()
    except OSError:
        return os.path.normpath(session_cwd) == os.path.normpath(current)
    return left == right or right in left.parents or left in right.parents


def slug_name(provider: str, session_id: str) -> str:
    token = session_id.replace("-", "")[:8].lower()
    name = f"h{provider[0]}{token}"
    return name[:32]


def resume_args(session: Session) -> tuple[str, list[str]]:
    if session.provider == "grok":
        return "grok", ["--resume", session.session_id]
    if session.provider == "claude":
        return "claude", ["--resume", session.session_id]
    if session.provider == "codex":
        return "codex", ["resume", session.session_id]
    if session.provider == "agy":
        return "agy", [f"--conversation={session.session_id}"]
    raise RuntimeError(f"unsupported provider {session.provider}")


def resume_session(session: Session, target_pane: str) -> str:
    if session.live_pane:
        herdr("agent", "focus", session.live_pane)
        return f"focused {session.live_pane}"
    cwd = session.cwd or workspace_cwd() or os.getcwd()
    split_from = target_pane or os.environ.get("HERDR_PANE_ID") or ""
    if split_from:
        created = herdr(
            "pane",
            "split",
            split_from,
            "--direction",
            "right",
            "--cwd",
            cwd,
            "--no-focus",
        )
    else:
        created = herdr("pane", "split", "--current", "--direction", "right", "--cwd", cwd, "--no-focus")
    pane = ""
    if isinstance(created, dict):
        pane_obj = created.get("pane") or {}
        pane = pick_id(pane_obj, "pane_id", "id")
    if not pane:
        raise RuntimeError("split did not return a pane id")
    kind, extra = resume_args(session)
    name = slug_name(session.provider, session.session_id)
    herdr("agent", "start", name, "--kind", kind, "--pane", pane, "--timeout", "60000", "--", *extra)
    return f"resumed in {pane} as {name}"


# --- TUI ----------------------------------------------------------------------


class HistoryTUI:
    def __init__(self, stdscr: curses.window) -> None:
        self.stdscr = stdscr
        self.cwd = workspace_cwd()
        self.target = invoker_pane()
        self.cwd_only = True
        self.query = ""
        self.searching = False
        self.cursor = 0
        self.scroll = 0
        self.status = ""
        self.sessions: list[Session] = []
        self.filtered: list[Session] = []
        self.reload()

    def reload(self) -> None:
        self.sessions = load_sessions()
        self.apply_filter()
        self.status = f"{len(self.filtered)}/{len(self.sessions)} sessions"

    def apply_filter(self) -> None:
        items = self.sessions
        if self.cwd_only and self.cwd:
            items = [item for item in items if same_cwd(item.cwd, self.cwd)]
        needle = self.query.strip().lower()
        if needle:
            items = [
                item
                for item in items
                if needle in item.title.lower()
                or needle in item.preview.lower()
                or needle in item.cwd.lower()
                or needle in item.provider
                or needle in item.session_id.lower()
            ]
        self.filtered = items
        if self.cursor >= len(self.filtered):
            self.cursor = max(0, len(self.filtered) - 1)
        if self.cursor < 0:
            self.cursor = 0

    def current(self) -> Session | None:
        if not self.filtered:
            return None
        return self.filtered[self.cursor]

    def draw(self) -> None:
        stdscr = self.stdscr
        stdscr.erase()
        height, width = stdscr.getmaxyx()
        list_width = max(28, min(46, width // 2))
        if width < 60:
            list_width = max(18, width - 2)
        preview_width = max(0, width - list_width - 1)
        now = time.time()
        header = "Sessions  cwd" if self.cwd_only else "Sessions  all"
        if self.searching:
            header = f"/{self.query}▌"
        stdscr.addstr(0, 0, clip(header, width), curses.A_BOLD)
        path = clip(self.cwd or "(no cwd)", width)
        if height > 2:
            stdscr.addstr(1, 0, path, curses.A_DIM)

        body_top = 2
        body_bottom = height - 2
        body_h = max(1, body_bottom - body_top)
        if self.cursor < self.scroll:
            self.scroll = self.cursor
        if self.cursor >= self.scroll + body_h:
            self.scroll = self.cursor - body_h + 1

        visible = self.filtered[self.scroll : self.scroll + body_h]
        if not visible:
            stdscr.addstr(body_top, 0, clip("No sessions for this filter.", list_width))
        for offset, session in enumerate(visible):
            row = body_top + offset
            selected = (self.scroll + offset) == self.cursor
            mark = "●" if session.live_pane else " "
            line = (
                f"{mark}{LABEL.get(session.provider, session.provider):<4} "
                f"{rel_time(session.updated_at, now):>4}  {session.title or '(untitled)'}"
            )
            attr = curses.A_REVERSE if selected else curses.A_NORMAL
            try:
                stdscr.addstr(row, 0, pad(line, list_width), attr)
            except curses.error:
                pass

        if preview_width >= 16:
            session = self.current()
            preview_lines = self.preview_lines(session, body_h, preview_width)
            for offset, line in enumerate(preview_lines):
                try:
                    stdscr.addnstr(body_top + offset, list_width + 1, line, preview_width)
                except curses.error:
                    pass
            for row in range(body_top, body_bottom):
                try:
                    stdscr.addch(row, list_width, curses.ACS_VLINE)
                except curses.error:
                    pass

        footer = "↑↓ move  enter resume  / search  a all/cwd  r refresh  q close"
        if self.status:
            footer = clip(self.status, width - 1) + "  |  " + footer
        try:
            stdscr.addstr(height - 1, 0, clip(footer, width - 1), curses.A_DIM)
        except curses.error:
            pass
        stdscr.refresh()

    def preview_lines(self, session: Session | None, height: int, width: int) -> list[str]:
        if session is None:
            return ["Select a session."]
        lines = [
            f"{session.provider}  {session.session_id}",
            session.cwd or "(cwd unknown)",
            f"{'LIVE ' + session.live_pane if session.live_pane else 'saved'}  "
            f"{datetime.fromtimestamp(session.updated_at).strftime('%Y-%m-%d %H:%M') if session.updated_at else ''}",
            "",
        ]
        body = session.preview.strip() or "(no preview)"
        for raw in body.splitlines():
            text = raw.rstrip()
            if not text:
                lines.append("")
                continue
            while display_width(text) > width:
                chunk = clip(text, width)
                lines.append(chunk)
                text = text[len(chunk) :].lstrip()
            lines.append(text)
            if len(lines) >= height:
                break
        return lines[:height]

    def resume(self) -> None:
        session = self.current()
        if session is None:
            self.status = "nothing selected"
            return
        self.status = f"resuming {session.provider}…"
        self.draw()
        try:
            self.status = resume_session(session, self.target)
            attach_live(self.sessions)
            self.apply_filter()
        except Exception as exc:  # noqa: BLE001
            self.status = str(exc).splitlines()[0][:120]

    def run(self) -> None:
        curses.curs_set(0)
        curses.use_default_colors()
        self.stdscr.nodelay(False)
        self.stdscr.keypad(True)
        curses.mousemask(curses.ALL_MOUSE_EVENTS)
        while True:
            self.draw()
            key = self.stdscr.getch()
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
            elif key in (curses.KEY_DOWN, ord("j")):
                self.cursor = min(max(0, len(self.filtered) - 1), self.cursor + 1)
            elif key == curses.KEY_PPAGE:
                self.cursor = max(0, self.cursor - 10)
            elif key == curses.KEY_NPAGE:
                self.cursor = min(max(0, len(self.filtered) - 1), self.cursor + 10)
            elif key in (10, 13):
                self.resume()
            elif key == ord("/"):
                self.searching = True
                self.query = ""
            elif key == ord("a"):
                self.cwd_only = not self.cwd_only
                self.apply_filter()
                self.status = "this workspace" if self.cwd_only else "all workspaces"
            elif key == ord("r"):
                self.reload()
            elif key == curses.KEY_MOUSE:
                try:
                    _, x, y, _, bstate = curses.getmouse()
                except curses.error:
                    continue
                height, width = self.stdscr.getmaxyx()
                list_width = max(28, min(46, width // 2))
                if y >= 2 and y < height - 1 and x < list_width:
                    index = self.scroll + (y - 2)
                    if 0 <= index < len(self.filtered):
                        self.cursor = index
                        if bstate & curses.BUTTON1_DOUBLE_CLICKED:
                            self.resume()
            elif key == curses.KEY_RESIZE:
                continue


def cmd_list(show_all: bool) -> int:
    cwd = workspace_cwd()
    sessions = load_sessions()
    rows = sessions if show_all else [item for item in sessions if same_cwd(item.cwd, cwd)]
    payload = [
        {
            "provider": item.provider,
            "id": item.session_id,
            "title": item.title,
            "cwd": item.cwd,
            "updated_at": item.updated_at,
            "live_pane": item.live_pane,
        }
        for item in rows[:80]
    ]
    json.dump({"cwd": cwd, "count": len(rows), "sessions": payload}, sys.stdout, ensure_ascii=False, indent=2)
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
    if path and pane:
        path.write_text(pane)


def cmd_open() -> int:
    pane = invoker_pane()
    cwd = workspace_cwd()
    env_args: list[str] = []
    if pane:
        env_args.extend(["--env", f"HERDR_SESSION_HISTORY_TARGET={pane}"])
    if cwd:
        env_args.extend(["--env", f"HERDR_SESSION_HISTORY_CWD={cwd}"])
    stored = state_pane_file()
    if stored and stored.is_file():
        existing = stored.read_text().strip()
        if existing:
            try:
                herdr("plugin", "pane", "focus", existing)
                return 0
            except RuntimeError:
                stored.unlink(missing_ok=True)
    herdr(
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
        "--focus",
        *env_args,
    )
    return 0


def cmd_tui() -> int:
    remember_pane()
    try:
        curses.wrapper(lambda stdscr: HistoryTUI(stdscr).run())
    except KeyboardInterrupt:
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
