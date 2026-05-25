# Claude Code Hook-Based Completion & Progress Detection

## Status: ✅ Production (cc_sdk.py v3.2, 2026-05-25)

## 架构

```
CC 使用工具
  → PostToolUse hook 事件
  → hermes_hook.py 写入:
    - {session_id}-PostToolUse-{ts}.json（单次记录）
    - {session_id}-progress.json（统一进度快照）
  → cc_sdk.py 扫描所有 PostToolUse → build_progress_report()
  → 进度可查: python3 cc_sdk.py --progress <session_id>

CC 完成任务
  → Stop hook 事件
  → hermes_hook.py 写入 {session_id}.json（覆盖，含 transcript 统计）
  → cc_sdk.py 检测到 Stop → 立即返回（不等 timeout）

CC 需要注意
  → Notification hook 事件
  → hermes_hook.py 写入 {session_id}-Notification-{ts}.json
```

## 实现文件

| 文件 | 用途 |
|------|------|
| `~/.claude/settings.json` | Hooks 配置（NOT 插件系统） |
| `~/.local/bin/hermes_hook.py` | Hook 脚本 |
| `~/.local/bin/cc_sdk.py` | SDK bridge（监控状态文件） |

## Hook 配置

在 `~/.claude/settings.json` 中：

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

## 状态文件

位置：`/tmp/cc-bridge-status/`

| 文件模式 | 写入时机 | 用途 |
|---------|---------|------|
| `{session_id}.json` | Stop | 完成信号，含 stats |
| `{session_id}-PostToolUse-{ts}.json` | 每次工具调用 | 单次记录 |
| `{session_id}-progress.json` | 每次 PostToolUse | 统一进度快照 |
| `{session_id}-Notification-{ts}.json` | CC 等待 | 权限提示 |
| `{session_id}-SubagentStop-{ts}.json` | 子 agent 完成 | 子任务状态 |

## 进度文件格式

`{session_id}-progress.json`：

```json
{
  "session_id": "ea836798-...",
  "elapsed": 45.2,
  "completed": false,
  "total_tool_calls": 12,
  "tool_breakdown": {"Read": 5, "Edit": 6, "Bash": 1},
  "last_tool": "Edit",
  "updated_at": "2026-05-25 10:35:00"
}
```

`completed: true` 时 `stop_stats` 包含：
```json
{
  "total_turns": 93,
  "total_tool_calls": 45,
  "tools_used": ["Read", "Edit", "Bash"],
  "files_modified": ["src/App.tsx"],
  "errors": []
}
```

## 使用方式

```bash
# 默认：hooks 启用
python3 cc_sdk.py "task" --max-turns 8 --timeout 180 --json

# 查询实时进度
python3 cc_sdk.py --progress <session_id>

# 禁用 hooks
python3 cc_sdk.py "task" --no-hooks --json

# bare 模式（⚠️ 禁用 hooks）
python3 cc_sdk.py "task" --bare --no-hooks --json
```

## 设计决策

1. **Stop 事件 = 完成信号**，不是 timeout
2. **max_turns/timeout = 安全兜底**，不是进度指标
3. **进度文件原子写入**（先 .tmp 再 rename）
4. **进度文件在完成后保留**，供事后分析
5. **PostToolUse 用独立文件**，不覆盖 Stop 信号
6. **Hook 在 CLI 和 SDK 模式下都触发**

## 性能

| 模式 | 启动时间 | Hook 事件 | 用途 |
|------|---------|-----------|------|
| 默认（hooks 开） | ~5s | ✅ 全部 | 生产任务 |
| `--bare --no-hooks` | ~3.2s | ❌ 无 | 快速查询 |

差异 ~1.7s，对实际任务（30-180s）可忽略。
