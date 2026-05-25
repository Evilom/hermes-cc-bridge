# hermes-cc-bridge

**Real-time progress tracking & hook-based completion detection for Claude Code SDK**

> 给 Claude Code 装上进度条 —— 实时知道它在做什么、做完了没有

[English](#english) | [中文](#中文)

---

## English

### The Problem

Claude Code is powerful, but blind. When you fire off a task:

- ❌ No progress feedback — is it working or stuck?
- ❌ `timeout` as the only signal — guess 300s and hope
- ❌ `max_turns` as progress proxy — meaningless metric
- ❌ No way to know what tools CC is using in real-time

### The Solution

**hermes-cc-bridge** hooks into Claude Code's event system to give you:

```
CC uses a tool  →  you see: [45s] tool #12: Edit | breakdown: {Read:5, Edit:6, Bash:1}
CC finishes     →  you get: instant Stop signal (not timeout guess)
CC needs input  →  you know: Notification event with reason
```

### Features

| Feature | Description |
|---------|-------------|
| 🔴 **Real-time Progress** | See every tool call as it happens — Read, Edit, Bash, Write... |
| ✅ **Instant Completion** | Stop hook fires the moment CC finishes — no timeout guessing |
| 📊 **Tool Breakdown** | Structured stats: which tools, how many, what files |
| 🔄 **Session Resume** | Get `session_id` from any run, resume from where CC left off |
| 📁 **Atomic Progress Files** | Written to disk safely, persist after completion |
| 🐍 **Python API** | `await run_task(prompt, cwd, max_turns)` — async streaming |
| ⚡ **CLI & SDK** | Works with both `claude -p` and `claude-code-sdk` |

### Architecture

```
┌─────────────────┐         hook events          ┌───────────────────┐
│  Claude Code    │ ────────────────────────────→ │  hermes_hook.py   │
│  (SDK or CLI)   │   PostToolUse / Stop / etc.   │  writes JSON to   │
└─────────────────┘                               │  /tmp/cc-bridge/  │
         │                                        └───────────────────┘
         │ SDK stream                                         │
         ▼                                                    ▼
┌─────────────────┐                               ┌───────────────────┐
│   cc_sdk.py     │ ←──────────────────────────── │  status files +   │
│   reads hooks   │      polls every 0.5s         │  progress file    │
│   + SDK stream  │                               └───────────────────┘
└─────────────────┘
         │
         ▼
   JSON result:
   {
     "success": true,
     "text": "Done! Modified 3 files...",
     "tool_count": 12,
     "session_id": "ea836798-...",
     "progress": {
       "elapsed": 45.2,
       "completed": true,
       "tool_breakdown": {"Read": 5, "Edit": 6, "Bash": 1}
     }
   }
```

### Quick Start

```bash
# 1. Install
pip3 install claude-code-sdk
npm install -g @anthropic-ai/claude-code

# 2. Clone & setup
git clone https://github.com/Evilom/hermes-cc-bridge.git
cd hermes-cc-bridge
cp scripts/cc_sdk.py scripts/hermes_hook.py ~/.local/bin/

# 3. Configure hooks (add to ~/.claude/settings.json)
#    See examples/settings.json for the full config

# 4. Run
python3 ~/.local/bin/cc_sdk.py "Fix the auth bug" \
  --cwd /your/project --max-turns 8 --timeout 180 --json

# 5. Check progress (from another terminal)
python3 ~/.local/bin/cc_sdk.py --progress <session_id>
```

---

## 中文

### 解决什么问题

Claude Code 很强，但是"盲"的。发一个任务出去：

- ❌ **没有进度反馈** —— 它在干活还是卡住了？
- ❌ **靠 timeout 猜** —— 设 300 秒，到了就杀，可能刚好写完最后一步
- ❌ **max_turns 不是进度** —— 10 轮不代表 10% 完成
- ❌ **不知道在用什么工具** —— 是在读文件还是在跑命令？

### 怎么解决

**hermes-cc-bridge** 利用 Claude Code 的 Hook 事件系统，实现真正的进度追踪：

```
CC 调用工具  → 你看到: [45s] tool #12: Edit | 分布: {Read:5, Edit:6, Bash:1}
CC 完成任务  → 你收到: 立即 Stop 信号（不用等 timeout）
CC 等待输入  → 你知道: Notification 事件 + 原因
```

### 核心功能

| 功能 | 说明 |
|------|------|
| 🔴 **实时进度** | 每个工具调用实时可见 —— Read、Edit、Bash、Write... |
| ✅ **即时完成** | Stop hook 在 CC 完成的瞬间触发，不靠 timeout 猜 |
| 📊 **工具统计** | 结构化数据：用了哪些工具、各多少次、改了什么文件 |
| 🔄 **会话恢复** | 任何运行都返回 `session_id`，超时后 resume 继续 |
| 📁 **原子写入** | 进度文件安全写入磁盘，完成后保留供事后分析 |
| 🐍 **Python API** | `await run_task(prompt, cwd, max_turns)` —— 异步流式 |
| ⚡ **CLI + SDK** | `claude -p` 和 `claude-code-sdk` 都能用 |

### 进度文件长什么样

```json
{
  "session_id": "ea836798-...",
  "elapsed": 45.2,
  "completed": false,
  "total_tool_calls": 12,
  "tool_breakdown": {"Read": 5, "Edit": 6, "Bash": 1},
  "last_tool": {"tool": "Edit", "input": "{'file_path': 'src/auth.py'}"},
  "updated_at": "2026-05-25 10:35:00"
}
```

完成后 `completed: true`，`stop_stats` 填充完整统计：

```json
{
  "total_turns": 93,
  "total_tool_calls": 45,
  "tools_used": ["Read", "Glob", "Edit", "Bash"],
  "files_modified": ["src/App.tsx", "src/auth.py"]
}
```

### 快速开始

```bash
# 1. 安装依赖
pip3 install claude-code-sdk
npm install -g @anthropic-ai/claude-code

# 2. 克隆 & 部署
git clone https://github.com/Evilom/hermes-cc-bridge.git
cd hermes-cc-bridge
cp scripts/cc_sdk.py scripts/hermes_hook.py ~/.local/bin/

# 3. 配置 hooks（加到 ~/.claude/settings.json）
#    参考 examples/settings.json

# 4. 运行任务
python3 ~/.local/bin/cc_sdk.py "修复 auth 模块的 bug" \
  --cwd /your/project --max-turns 8 --timeout 180 --json

# 5. 查询进度（另一个终端）
python3 ~/.local/bin/cc_sdk.py --progress <session_id>
```

### 使用场景

**场景 1：大任务不瞎等**
```bash
# 发任务
python3 cc_sdk.py "重构整个认证模块" --max-turns 15 --timeout 600 --json &

# 每隔几秒看一眼进度
python3 cc_sdk.py --progress <session_id>
# → {"total_tool_calls": 8, "tool_breakdown": {"Read": 3, "Edit": 4, "Bash": 1}, "elapsed": 32.1}
```

**场景 2：超时不丢工作**
```bash
# 第一次跑，超时了
RESULT=$(python3 cc_sdk.py "复杂任务" --max-turns 10 --json)
SESSION_ID=$(echo "$RESULT" | python3 -c "import json,sys; print(json.load(sys.stdin)['session_id'])")

# 继续，不重头来
python3 cc_sdk.py "继续之前的工作" --resume "$SESSION_ID" --max-turns 5 --json
```

**场景 3：Python 集成**
```python
import asyncio
from cc_sdk import run_task

async def deploy_check():
    result = await run_task(
        prompt="Run all tests and fix any failures",
        cwd="/app",
        max_turns=12,
        timeout=300,
    )
    
    if result["success"]:
        tools = result["progress"]["tool_breakdown"]
        print(f"✅ Done in {result['elapsed_seconds']}s, tools: {tools}")
    else:
        print(f"❌ Failed: {result['errors']}")

asyncio.run(deploy_check())
```

---

## CLI Reference

| Flag | Default | Description / 说明 |
|------|---------|-------------------|
| `--cwd` | `.` | Working directory / 工作目录 |
| `--max-turns` | 5 | Max agentic turns / 最大轮次 |
| `--timeout` | 180 | Timeout seconds / 超时秒数 |
| `--tools` | all | Allowed tools (comma) / 允许的工具 |
| `--effort` | medium | low/medium/high/max / 推理深度 |
| `--json` | off | JSON output / JSON 输出 |
| `--quiet` | off | No stderr streaming / 静默模式 |
| `--resume` | none | Resume session / 恢复会话 |
| `--continue` | off | Continue latest / 继续最近会话 |
| `--progress` | none | Query live progress / 查询实时进度 |
| `--model` | none | Model override / 模型覆盖 |
| `--bare` | off | ⚠️ Faster but disables hooks / 快但禁用 hooks |

## Status Files

Location: `/tmp/cc-bridge-status/` (config via `CC_BRIDGE_STATUS_DIR`)

| Pattern | When / 触发 | Purpose / 用途 |
|---------|-------------|---------------|
| `{id}.json` | Stop | Completion signal / 完成信号 |
| `{id}-PostToolUse-{ts}.json` | Each tool | Tool record / 工具记录 |
| `{id}-progress.json` | Each PostToolUse | Unified snapshot / 统一进度快照 |
| `{id}-Notification-{ts}.json` | Needs attention | Input prompt / 等待输入 |

## Requirements

- Python 3.10+
- `claude-code-sdk` (`pip3 install claude-code-sdk`)
- Claude Code CLI (`npm install -g @anthropic-ai/claude-code`)

## License

MIT
