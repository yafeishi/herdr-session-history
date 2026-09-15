# Herdr Session History

Compact session rail for Herdr, in the Codex / ChatGPT history style: one small row per conversation, a hover card with a preview, click to jump to that session.

Supports **Grok**, **Claude Code**, **Codex**, and **Agy**.

## Use

Inside Herdr: `prefix+shift+h`

Or:

```sh
herdr plugin action invoke herdr-session-history.open
```

| Key | Action |
| --- | --- |
| `↑` `↓` / `j` `k` | Browse history on this tab (preview card only) |
| click / `enter` | Switch the current conversation pane in place — no new tab |
| `/` | Search title / preview / cwd |
| `a` | Toggle this workspace vs all |
| `r` | Reload |
| `q` / `esc` | Close |

Default filter is the current workspace directory (and its subfolders), like `codex resume` without `--all`.

## Install

```sh
herdr plugin link /Users/dang/.local/src/herdr-session-history
```

Requires Herdr 0.9+ and Python 3.
