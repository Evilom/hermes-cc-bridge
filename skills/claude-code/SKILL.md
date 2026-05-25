---
name: claude-code
description: "Delegate coding to Claude Code CLI (features, PRs)."
version: 4.0.0
author: Hermes Agent + Teknium
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Coding-Agent, Claude, Anthropic, Code-Review, Refactoring, PTY, Automation]
    related_skills: [codex, hermes-agent, opencode]
---

# Claude Code — Hermes Orchestration Guide v4

Delegate coding tasks to [Claude Code](https://code.claude.com/docs/en/cli-reference) via the Hermes terminal. CC can read files, write code, run shell commands, spawn subagents, and manage git workflows autonomously.

## Prerequisites

- **Install:** `npm install -g @anthropic-ai/claude-code`
- **Auth:** `claude` once (browser OAuth), or `ANTHROPIC_API_KEY`, or `claude auth login --console`
- **Health:** `claude doctor`, `claude auth status`, `claude --version` (v2.x+)

## 核心原则：进度 = Hook 事件，不是 timeout

**Stop 事件 = 完成信号。max_turns/timeout 只是安全兜底，不是进度指标。**

```
CC 使用工具 → PostToolUse hook → 进度文件更新
CC 完成任务 → Stop hook → 完成信号
CC 需要注意 → Notification hook → 权限/输入等待
```

查询进度：`python3 ~/.local/bin/cc_sdk.py --progress <session_id>`

---

## 调用方式

### 方式 0：SDK Bridge（首选，所有自动化调用必须用这个）

```bash
python3 ~/.local/bin/cc_sdk.py "task description" \
  --cwd ~/project --max-turns 8 --timeout 180 --json
```

**为什么用 SDK 而不是 CLI `--print`：**
- SDK 流式输出，不阻塞
- Hook 感知完成（Stop 事件），不靠 timeout 猜
- `permission_mode="bypassPermissions"` 自动设置
- JSON 结果含 `session_id`（用于 resume）和 `progress`（实时进度）

### 方式 1：Print Mode（`-p`）— 简单一次性任务

```bash
claude -p 'Fix the bug in src/auth.py' --allowedTools 'Read,Edit' --max-turns 5
```

### 方式 2：Interactive PTY（tmux）— 多轮对话

```bash
tmux new-session -d -s cc-work -x 140 -y 40
tmux send-keys -t cc-work 'cd /path && claude' Enter
sleep 5 && tmux send-keys -t cc-work 'your task' Enter
```

---

## 实时进度追踪系统

### 架构

```
Hook 脚本 (hermes_hook.py)
  ├─ PostToolUse → {session_id}-PostToolUse-{ts}.json（每个工具调用一个文件）
  ├─ PostToolUse → {session_id}-progress.json（统一进度文件，每次更新）
  ├─ Stop → {session_id}.json（完成信号，覆盖写入）
  ├─ Notification → {session_id}-Notification-{ts}.json
  └─ SubagentStop → {session_id}-SubagentStop-{ts}.json

SDK Bridge (cc_sdk.py)
  ├─ wait_for_stop_event() — 轮询 Stop 文件 + 扫描 PostToolUse 文件
  ├─ build_progress_report() — 聚合所有事件为结构化进度
  ├─ --progress SESSION_ID — 只读查询，立即退出
  └─ 结果 JSON 的 progress 字段 — 含工具分布、耗时、完成状态
```

### 状态文件位置

`/tmp/cc-bridge-status/`

| 文件 | 写入时机 | 用途 |
|------|---------|------|
| `{session_id}.json` | Stop 事件 | 完成信号，含 transcript 统计 |
| `{session_id}-PostToolUse-{ts}.json` | 每次工具调用 | 单次工具记录 |
| `{session_id}-progress.json` | 每次 PostToolUse | 统一进度快照 |
| `{session_id}-Notification-{ts}.json` | CC 等待注意 | 权限/输入提示 |
| `{session_id}-SubagentStop-{ts}.json` | 子 agent 完成 | 子任务状态 |

### 查询进度

```bash
python3 ~/.local/bin/cc_sdk.py --progress <session_id>

# 返回：
{
  "session_id": "ea836798-...",
  "elapsed": 45.2,
  "completed": false,
  "total_tool_calls": 12,
  "tool_breakdown": {"Read": 5, "Edit": 6, "Bash": 1},
  "last_tool": "Edit",
  "stop_stats": {}
}
```

`completed: true` 时 `stop_stats` 包含 turns/tools_used/files_modified。

### Hook 配置

在 `~/.claude/settings.json`（NOT 插件系统）：

```json
{
  "hooks": {
    "Stop": [{"matcher": "", "hooks": [{"type": "command", "command": "python3 ~/.local/bin/hermes_hook.py", "timeout": 10}]}],
    "PostToolUse": [{"matcher": "", "hooks": [{"type": "command", "command": "python3 ~/.local/bin/hermes_hook.py", "timeout": 5}]}],
    "Notification": [{"matcher": "", "hooks": [{"type": "command", "command": "python3 ~/.local/bin/hermes_hook.py", "timeout": 10}]}],
    "SubagentStop": [{"matcher": "", "hooks": [{"type": "command", "command": "python3 ~/.local/bin/hermes_hook.py", "timeout": 10}]}]
  }
}
```

**⚠️ hooks 在 settings.json 里，不是 `.claude/plugins/`。插件系统需要 marketplace 安装，settings.json hooks 立即生效。**

---

## SDK Bridge 详细用法

### CLI 参数

```bash
python3 ~/.local/bin/cc_sdk.py "prompt" [选项]
```

| 参数 | 默认 | 说明 |
|------|------|------|
| `--cwd` | `.` | 工作目录 |
| `--max-turns` | 5 | 最大 agentic 轮次 |
| `--timeout` | 180 | 超时秒数 |
| `--tools` | all | 允许的工具（逗号分隔） |
| `--disallowed-tools` | none | 禁用的工具 |
| `--effort` | medium | low/medium/high/max |
| `--json` | off | JSON 输出 |
| `--quiet` | off | 不流式输出到 stderr |
| `--bare` | off | ⚠️ 更快启动但禁用 hooks |
| `--no-hooks` | off | 禁用 hook 监听 |
| `--resume` | none | 继续之前的会话 |
| `--continue` | off | 继续当前目录最近会话 |
| `--progress` | none | 查询运行中任务的实时进度（只读） |
| `--model` | none | 模型覆盖 |
| `--system-prompt` | none | 替换系统提示 |
| `--append-system-prompt` | none | 追加系统提示 |
| `--mcp-config` | none | MCP 服务器配置 JSON |
| `--env` | none | 环境变量 JSON |

### 典型调用

```bash
# 读取分析
python3 ~/.local/bin/cc_sdk.py "Analyze src/core/" \
  --cwd ~/project --max-turns 3 --tools Read --timeout 30 --json

# 写文件
python3 ~/.local/bin/cc_sdk.py "Create src/utils/helper.ts" \
  --cwd ~/project --max-turns 5 --tools Read,Write --timeout 60 --json

# 复杂重构
python3 ~/.local/bin/cc_sdk.py "Refactor auth module" \
  --cwd ~/project --max-turns 10 --tools Read,Write,Edit,Bash --timeout 300 --json

# 查询进度（另一个终端）
python3 ~/.local/bin/cc_sdk.py --progress <session_id>
```

### Python SDK 直接调用（高级）

```python
import asyncio
from claude_code_sdk import query, ClaudeCodeOptions

async def cc_task(prompt, cwd, max_turns=5, tools=None):
    result_text = ""
    async for msg in query(prompt=prompt, options=ClaudeCodeOptions(
        max_turns=max_turns, cwd=cwd,
        permission_mode="bypassPermissions",
        allowed_tools=tools,
    )):
        if hasattr(msg, 'content') and isinstance(msg.content, list):
            for block in msg.content:
                if hasattr(block, 'text'):
                    result_text = block.text
    return result_text
```

---

## 任务分级与参数

| 任务类型 | --max-turns | --effort | timeout | --tools |
|---------|-------------|----------|---------|---------|
| 读取分析 | 2-3 | low | 30s | Read |
| 写 1 个文件 | 3-5 | low | 60s | Read,Write |
| 编辑 1-2 个文件 | 5-8 | medium | 120s | Read,Edit |
| 多文件重构 | 10-12 | medium | 300s | Read,Edit,Write,Bash |
| 新功能 | 8-12 | high | 300s | Read,Edit,Write,Bash |
| 代码审查 | 5 | medium | 120s | Read |
| 跑测试+修复 | 10-12 | medium | 300s | Read,Edit,Bash |

**关键数字（tested）：**
- 1 轮：~4-6s
- 3 轮（中等任务）：~24s
- 安全预算：`--max-turns 8-12` + `--effort medium` + timeout 120-180s
- 危险组合：`--max-turns 20+` + `--effort high`（几乎必超时）

---

## 超时恢复模式

**Timeout ≠ Failure。CC 写的文件在磁盘上，超时不会回滚。**

### 恢复步骤

1. `git diff --stat` — 看 CC 改了什么
2. `ls -la` — 看 CC 创建了什么
3. `npx tsc --noEmit` — 编译检查
4. 读关键文件 — 可能 80-100% 完成

### Resume 模式

```bash
# 第一次调用，返回 session_id
RESULT=$(python3 ~/.local/bin/cc_sdk.py "complex task" --max-turns 10 --json)
SESSION_ID=$(echo "$RESULT" | python3 -c "import json,sys; print(json.load(sys.stdin)['session_id'])")

# 超时后 resume
python3 ~/.local/bin/cc_sdk.py "continue your work" --resume "$SESSION_ID" --max-turns 5 --json
```

### 拆分策略

```
❌ Bad:  "implement 5 features" --max-turns 25 --effort high
✅ Good: 
  1. "implement feature A" --max-turns 8 --effort medium → verify
  2. "implement feature B" --max-turns 8 --effort medium → verify
  3. 如果超时: "continue" --resume <id> --max-turns 5 → verify
```

---

## ⚠️ 不要用 --bare 除非确定不需要 hooks

`--bare` 设置 `CLAUDE_CODE_SIMPLE=1`，跳过 hooks/LSP/插件/CLAUDE.md。启动快 ~1.7s，但失去：
- Stop 事件完成检测
- PostToolUse 进度追踪
- Notification 权限提示

**只有这些场景用 bare：** 一次性快速查询、测试/debug、不需要 hook 感知的任务。

---

## SDK extra_args 陷阱

- `extra_args` 必须是 **dict**，不是 list
- 布尔标志（如 `--bare`）不能通过 `extra_args` 传
- `--bare` 模式用 SDK：`env={'CLAUDE_CODE_SIMPLE': '1'}`
- key-value 标志：`extra_args={"mcp-config": "path"}`
- ❌ `extra_args=["--bare"]` → crash
- ❌ `extra_args={"--bare": True}` → `----bare` 错误

---

## CLAUDE.md 策略

项目根目录的 `CLAUDE.md` 会被 CC 自动加载。这是**最有效的优化**——每个 prompt 都更短。

```markdown
# Project: AntDesk
- Tauri 2 + React + TypeScript + Rust
- Build: yarn build (frontend), cargo check (backend)
- Glass effect: CSS backdrop-filter with transparent html/body
- Drag: use window.startDragging() not IPC
```

**没 CLAUDE.md**：每个 prompt 需要 200+ 词上下文。
**有 CLAUDE.md**：每个 prompt 只需 2-3 句。

---

## Prompt 模板

```
❌ BAD（太长，太多任务）：
"Fix these 5 files: 1) src/a.tsx change X to Y, 2) src/b.ts change A to B..."

✅ GOOD（一个任务，最少上下文）：
"Edit src/fab.tsx: replace drag logic to use getCurrentWindow().startDragging()
from @tauri-apps/api/window instead of invoke('set_fab_position')."
```

---

## 多文件链式调用

```
# Step 1: 编辑文件 A
python3 cc_sdk.py "Edit src/fab.tsx: ..." --max-turns 5 --tools Read,Edit --timeout 120 --json

# Step 2: 编辑文件 B
python3 cc_sdk.py "Edit src/App.css: ..." --max-turns 5 --tools Read,Edit --timeout 120 --json

# Step 3: 构建验证
python3 cc_sdk.py "Run yarn build, fix any errors" --max-turns 8 --tools Read,Edit,Bash --timeout 300 --json
```

---

## 何时用 CC vs 直接工具

| 场景 | 用 CC | 用 Hermes 直接工具 |
|------|-------|-------------------|
| 多文件重构 | ✅ | ❌ |
| 新功能实现 | ✅ | ❌ |
| Bug 调查 | ✅ | ❌ |
| 单行修改 | ❌ | ✅ patch() |
| 简单替换 | ❌ | ✅ patch() |
| 配置编辑 | ❌ | ✅ write_file() |
| Shell 命令 | ❌ | ✅ terminal() |
| 文件读取 | ❌ | ✅ read_file() |

---

## CLI 标志速查

### Session
| 标志 | 说明 |
|------|------|
| `-p, --print` | 非交互模式 |
| `-c, --continue` | 继续当前目录最近会话 |
| `-r, --resume <id>` | 恢复指定会话 |
| `--fork-session` | resume 时创建新 session ID |
| `--session-id <uuid>` | 指定 UUID |

### Model & Performance
| 标志 | 说明 |
|------|------|
| `--model <alias>` | sonnet/opus/haiku 或完整名 |
| `--effort <level>` | low/medium/high/max/auto |
| `--max-turns <n>` | 限制 agentic 轮次 |
| `--max-budget-usd <n>` | 限制花费 |

### Permission
| 标志 | 说明 |
|------|------|
| `--dangerously-skip-permissions` | 自动批准所有工具 |
| `--permission-mode <mode>` | default/acceptEdits/plan/auto/dontAsk/bypassPermissions |
| `--allowedTools <tools...>` | 白名单工具 |
| `--disallowedTools <tools...>` | 黑名单工具 |

### Output
| 标志 | 说明 |
|------|------|
| `--output-format <fmt>` | text/json/stream-json |
| `--json-schema <schema>` | 结构化 JSON 输出 |
| `--verbose` | 逐轮输出 |

### System Prompt
| 标志 | 说明 |
|------|------|
| `--append-system-prompt <text>` | 追加到默认提示 |
| `--system-prompt <text>` | 替换整个提示 |
| `--bare` | 跳过 hooks/plugins/CLAUDE.md |

---

## Interactive 快捷键

| 键 | 功能 |
|----|------|
| `Ctrl+C` | 取消当前操作 |
| `Ctrl+D` | 退出 |
| `Ctrl+B` | 后台运行任务 |
| `Ctrl+O` | 查看 thinking 过程 |
| `Shift+Tab` | 切换权限模式 |
| `Alt+P` | 切换模型 |

---

## Hooks 完整列表

| Hook | 触发时机 | 用途 |
|------|---------|------|
| `UserPromptSubmit` | 处理用户输入前 | 输入验证 |
| `PreToolUse` | 工具执行前 | 安全门控（exit 2 = 阻止） |
| `PostToolUse` | 工具执行后 | 自动格式化、进度追踪 |
| `Notification` | 权限请求/等待输入 | 桌面通知 |
| `Stop` | CC 完成响应 | **完成信号** |
| `SubagentStop` | 子 agent 完成 | 子任务状态 |
| `PreCompact` | 上下文压缩前 | 备份 transcript |
| `SessionStart` | 会话开始 | 加载开发上下文 |

---

## MCP 集成

```bash
# 添加 GitHub MCP
claude mcp add -s user github -- npx @modelcontextprotocol/server-github

# 在 print mode 使用
claude -p 'Query database' --mcp-config mcp-servers.json --strict-mcp-config
```

---

## 成本优化

1. `--max-turns` 防止失控循环
2. `--max-budget-usd` 限制花费（最低 ~$0.05）
3. `--effort low` 简单任务更快更便宜
4. `--allowedTools` 限制到只需的工具
5. `/compact` 交互模式下压缩上下文
6. `--model haiku` 简单任务，`--model opus` 复杂任务

---

## Pitfalls

1. **Timeout ≠ Failure** — CC 写的文件在磁盘上，超时不会回滚。先检查文件再决定是否重跑。
2. **Reached max turns** — CC 可能已完成 80%。检查 git diff 再决定是否 resume。
3. **`--effort high` + `--max-turns 20` 几乎必超时** — 可靠组合是 `medium` + `10-12`。
4. **单个 prompt 放 5 个编辑任务** — 拆成原子任务，每个一次调用。
5. **CC 偶尔声称"已实现"** — 可能是误判，read_file 验证。
6. **CC 会创建临时文件** — 提交前 `git status` 检查。
7. **`--max-turns` 与 `--resume` 冲突** — resume 时 remove max_turns。
8. **`--text` 不是 `auth status` 的有效标志** — 直接用 `claude auth status`。
9. **`--max-budget-usd` 最低 ~$0.05** — 系统提示缓存就要这个价。
10. **非 Anthropic 后端的 stream 不稳定** — 本地代理 `127.0.0.1:8082` 加重试。
11. **delegate_task 的 acp_command: claude 也超时** — 直接用 SDK bridge。
12. **子 agent 分析可能有误** — 关键断言用 grep/terminal 验证。
13. **Hooks 在 settings.json 里，不是插件系统** — 插件需要 marketplace。
14. **`--bare` 禁用所有 hooks** — 确认不需要 hook 感知才用。
15. **PostToolUse 事件用独立文件** — Stop 用覆盖写入，互不干扰。
16. **max_turns/timeout 是安全兜底** — 不是进度指标。用 `--progress` 查实时状态。

---

## Reference Files

- `scripts/cc_sdk.py` — SDK bridge（v3.2, hook-aware, --progress 支持）
- `references/cc-hook-completion.md` — Hook 完成检测详细设置
- `references/python-sdk-guide.md` — SDK 高级用法
- `references/cc-proxy-solutions.md` — 非 Anthropic 后端代理方案
- `references/cc-switch-proxy.md` — CC Switch 代理调试
- `references/tauri2-ui-patterns.md` — Tauri 2 UI/UX 模式
- `references/tauri2-react-patterns.md` — Tauri 2 + React 项目结构
- `references/oes-web2-test-patterns.md` — Vitest 测试模式
- `references/game-dev-testing-patterns.md` — 游戏开发三层验证
- `references/notion-api-debugging.md` — Notion API 调试
