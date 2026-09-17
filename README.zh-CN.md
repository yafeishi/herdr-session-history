# Herdr Session History

[English](README.md)

给 [Herdr](https://herdr.dev) 用的紧凑会话历史栏。每个 agent pane 各自一份**当前 session 的轮次列表**。选中一行，会把正在运行的 Grok 滚动区跳到那条用户输入。

这是社区插件，不是 Herdr 核心的一部分。

## 截图

<p align="center">
  <img src="docs/screenshots/rail.png" alt="历史栏，选中的轮次旁有预览卡片" width="920" />
</p>

当前 pane 里每一轮占一小行。选中一行会打开预览卡片。

<p align="center">
  <img src="docs/screenshots/jump.png" alt="点击历史行，Grok 滚动区跳到对应的用户输入" width="920" />
</p>

点击或按上下键：当前 Grok pane 会跳到那条用户输入（`Tab`，再 `Shift+Left` / `Shift+Right`）。

<p align="center">
  <img src="docs/screenshots/independent.png" alt="两个 Herdr tab 各自显示不同的会话历史栏" width="920" />
</p>

每个 pane 自己的历史。另一个 tab 不会共用这份列表。

## 安装

需要 **Herdr 0.9+** 和 **Python 3**。

```sh
herdr plugin install yafeishi/herdr-session-history
```

绑定快捷键（可选）：

```toml
[[keys.command]]
key = "prefix+shift+h"
type = "plugin_action"
command = "herdr-session-history.open"
description = "打开会话历史"
```

重新加载配置：`herdr server reload-config`。

本地开发：

```sh
git clone https://github.com/yafeishi/herdr-session-history.git
herdr plugin link ./herdr-session-history
```

## 使用

先把焦点放到某个 agent pane，再按 `prefix+shift+h`（或执行 `herdr plugin action invoke herdr-session-history.open`）。

历史栏只绑定**当前这个 pane**。另一个 pane 或 tab 需要各自再开一条。关掉绑定的 agent pane 时，这条历史栏也会一起关掉。

焦点不在 agent pane 上时不会打开（避免绑到上一次的会话）。

| 按键 | 作用 |
| --- | --- |
| `↑` `↓` / `j` `k` | 在列表里移动；停一下后跳转到对应轮次 |
| 点击 / `enter` | 立刻跳到那一轮（`Tab`，再 `Shift+←/→`） |
| `/` | 在当前对话里搜索（支持中文） |
| `r` | 重新加载 |
| `q` / `esc` | 关掉这条历史栏 |

绑定的 agent 处于 `working` 时仍可跳转（Grok 回答中会把键盘停在滚动区）。

## 测试

```sh
python3 -m unittest discover -s tests -v
```

## Agent 支持

| Agent | 轮次列表 | 在直播 TUI 里跳转 |
| --- | --- | --- |
| Grok | 支持（`chat_history.jsonl`） | 支持 |
| Claude Code | 支持（session jsonl） | 尽力而为，同一套按键 |
| Codex | 暂无 | 会发按键；不保证 |
| Agy | 暂无 | 不支持 |

## 许可证

[MIT](LICENSE)
