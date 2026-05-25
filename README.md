# hermes-cc-bridge

Real-time progress tracking & hook-based completion detection for [Claude Code](https://code.claude.com/docs/en/cli-reference) SDK.

> **Problem:** Claude Code has no built-in progress feedback. You set `max_turns` and `timeout` and hope for the best.  
> **Solution:** Hook into CC's event system to get real-time tool usage, progress reports, and instant completion signals.

## Architecture

```
Claude Code uses a tool
  → PostToolUse hook event
  → hermes_hook.py writes progress file
  → cc_sdk.py reads it → structured progress report

Claude Code finishes
  → Stop hook event
  → hermes_hook.py writes completion file
  → cc_sdk.py detects it → instant return (no timeout guessing)
```

```
┌──────────────┐     hook events     ┌──────────────────┐
│ Claude Code  │ ──────────────────→ │ hermes_hook.py   │
│  (SDK/CLI)   │                     │  writes JSON to  │
└──────────────┘                     │  /tmp/cc-bridge/ │
       │                             └──────────────────┘
       │ SDK stream                            │
       ▼                                       ▼
┌──────────────┐                     ┌──────────────────┐
│  cc_sdk.py   │ ←────────────────── │ status files     │
│  reads hooks │   polls every 0.5s  │ + progress file  │
│  + stream    │                     └──────────────────┘
└──────────────┘
       │
       ▼
  JSON result with:
  - text output
  - tool uses
  - progress report
  - session_id (for resume)
```

## Quick Start

### 1. Install

```bash
# Clone
git clone https://github.com/Evilom/hermes-cc-bridge.git
cd hermes-cc-bridge

# Install Claude Code SDK
pip3 install claude-code-sdk

# Copy scripts to a permanent location
mkdir -p ~/.local/bin
cp scripts/cc_sdk.py ~/.local/bin/
cp scripts/hermes_hook.py ~/.local/bin/
chmod +x ~/.local/bin/cc_sdk.py ~/.local/bin/hermes_hook.py
```

### 2. Configure Hooks

Add to `~/.claude/settings.json`:

```json
{
  "hooks": {
    "Stop": [{"matcher": "", "hooks": [{"type": "command", "command": "python3 ~/.local/bin/hermes_hook.py", "timeout": 10}]}],
    "PostToolUse": [{"matcher": "", "hooks": [{"type": "command", "command": "python3 ~/.local/bin/hermes_hook.py", "timeout": 5}]}]
  }
}
```

### 3. Run

```bash
# Execute a task
python3 ~/.local/bin/cc_sdk.py "Fix the bug in auth.py" \
  --cwd /path/to/project --max-turns 8 --timeout 180 --json

# Query progress of a running task (from another terminal)
python3 ~/.local/bin/cc_sdk.py --progress <session_id>
```

## Usage

### CLI

```bash
python3 cc_sdk.py "your task prompt" [options]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--cwd` | `.` | Working directory |
| `--max-turns` | 5 | Max agentic turns |
| `--timeout` | 180 | Timeout in seconds |
| `--tools` | all | Allowed tools (comma-separated) |
| `--effort` | medium | low / medium / high / max |
| `--json` | off | JSON output |
| `--quiet` | off | No streaming to stderr |
| `--resume` | none | Resume a session by ID |
| `--continue` | off | Continue most recent session |
| `--progress` | none | Query live progress (read-only) |
| `--model` | none | Model override |
| `--bare` | off | Faster startup but disables hooks |
| `--no-hooks` | off | Disable hook monitoring |

### Progress Tracking

```bash
# Start a task
python3 cc_sdk.py "complex refactor" --cwd ~/project --max-turns 10 --timeout 300 --json &

# Check progress
python3 cc_sdk.py --progress <session_id>
```

Returns:

```json
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

When `completed: true`, `stop_stats` contains full transcript statistics.

### Python API

```python
import asyncio
from cc_sdk import run_task

async def main():
    result = await run_task(
        prompt="Refactor the auth module",
        cwd="/path/to/project",
        max_turns=10,
        timeout=300,
    )
    print(result["text"])
    print(f"Tools used: {result['progress']['tool_breakdown']}")

asyncio.run(main())
```

### Resume After Timeout

```bash
# First call
RESULT=$(python3 cc_sdk.py "complex task" --max-turns 10 --json)
SESSION_ID=$(echo "$RESULT" | python3 -c "import json,sys; print(json.load(sys.stdin)['session_id'])")

# Resume
python3 cc_sdk.py "continue your work" --resume "$SESSION_ID" --max-turns 5 --json
```

## Key Design Decisions

1. **Stop event = completion signal**, not timeout
2. **max_turns/timeout = safety nets** only, not progress indicators
3. **Progress files written atomically** (write to `.tmp`, then rename)
4. **Progress persists after completion** for post-mortem analysis
5. **PostToolUse uses separate files** per event, Stop uses overwrite — no clobbering

## Status Files

Location: `/tmp/cc-bridge-status/` (configurable via `CC_BRIDGE_STATUS_DIR` env var)

| Pattern | Trigger | Purpose |
|---------|---------|---------|
| `{session_id}.json` | Stop | Completion signal with stats |
| `{session_id}-PostToolUse-{ts}.json` | Each tool call | Individual tool record |
| `{session_id}-progress.json` | Each PostToolUse | Unified progress snapshot |
| `{session_id}-Notification-{ts}.json` | CC needs attention | Permission/input prompt |

## Requirements

- Python 3.10+
- `claude-code-sdk` (`pip3 install claude-code-sdk`)
- Claude Code CLI (`npm install -g @anthropic-ai/claude-code`)

## License

MIT
