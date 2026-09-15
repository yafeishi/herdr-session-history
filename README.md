# Herdr Session History

Codex-style session list for Herdr. Opens as a right-hand pane: past conversations on the left of that pane, preview on the right. Enter resumes the selected session.

Supports **Grok**, **Claude Code**, **Codex**, and **Agy**.

## Use

Inside Herdr: `prefix+shift+h`

Or:

```sh
herdr plugin action invoke herdr-session-history.open
```

| Key | Action |
| --- | --- |
| `↑` `↓` / `j` `k` | Move |
| `enter` | Resume (focus if already live, otherwise split and `--resume`) |
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
