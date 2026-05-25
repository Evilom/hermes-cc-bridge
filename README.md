# hermes-cc-bridge

**Real-time progress tracking & hook-based completion detection for Claude Code**

[English](README.md) | [中文](README_CN.md)

---

## The Problem: Claude Code is Blind

When you delegate a task to Claude Code via the SDK, you're flying blind:

**No progress feedback.** You send a task — "Refactor the auth module" — and then... nothing. Is CC reading files? Writing code? Running tests? Stuck in a loop? You don't know. You stare at a terminal that outputs nothing until it either succeeds or times out.

**Timeout is your only signal.** So you guess: "This task probably takes 300 seconds." If CC finishes in 30s, you waste 270s waiting. If CC needs 310s, you kill it at 300s — right before it would have finished. Either way, you lose.

**`max_turns` is meaningless.** Setting `max_turns=10` doesn't mean "10% done at turn 1." CC might do 80% of the work in 3 turns and spend 7 more on verification. Or it might use all 10 turns and only get halfway. You can't tell.

**Timeout ≠ failure.** This is the worst part. When CC times out, the files it already wrote are still on disk. The work is done — but you think it failed, so you retry from scratch, wasting time and tokens. We've seen tasks that were 100% complete at timeout, but the post-completion verification step got killed.

**No session continuity.** You can't ask "what just happened?" after a run. No structured output of what tools were used, what files were touched, how long it took. Just a wall of text or a timeout error.

### What This Looks Like in Practice

```
# The old way: guess, wait, hope
$ python3 cc_sdk.py "Big refactor" --max-turns 15 --timeout 600 --json

# ... 10 minutes of silence ...

# Either:
#   ✅ {"success": true, ...}  (but you waited 10 min for a 2-min task)
#   ❌ {"errors": ["TIMEOUT after 600s"]}  (but the work was actually done)
#   ❌ {"errors": ["Reached max turns"]}  (but 80% was completed)
```

You have no idea which outcome you'll get. And when it fails, you don't know how far CC got before it stopped.

---

## The Solution: hermes-cc-bridge

**hermes-cc-bridge** hooks into Claude Code's event system to give you real-time visibility into what CC is doing.

### Before vs After

| | Before (raw SDK) | After (with hermes-cc-bridge) |
|---|---|---|
| **Progress** | None. Wait for timeout or success. | Real-time. See every tool call as it happens. |
| **Completion signal** | Timeout or SDK stream end | Stop hook fires instantly when CC finishes |
| **"Is it stuck?"** | Can't tell. Wait and guess. | Check `--progress` — see live tool count & breakdown |
| **Timeout failure** | Assume task failed. Retry from scratch. | Check files on disk. Resume with `--resume` if needed. |
| **What CC did** | Wall of text output | Structured JSON: tools used, files touched, elapsed time |
| **Session continuity** | Lost after timeout | `session_id` preserved, resume from last checkpoint |

### What You See Now

```
$ python3 cc_sdk.py "Big refactor" --max-turns 15 --timeout 600 --json &

# Check progress from another terminal:
$ python3 cc_sdk.py --progress <session_id>

# Output:
[CC Progress] 12s | tool #3: Read | breakdown: {Read: 3}
[CC Progress] 28s | tool #6: Edit | breakdown: {Read: 4, Edit: 2}
[CC Progress] 45s | tool #12: Bash | breakdown: {Read: 5, Edit: 6, Bash: 1}

# When done:
[CC Progress] ✅ Done! 67s | turns=8 | tools=14 | used: Read, Edit, Bash
```

No more guessing. You know exactly what CC is doing, how long it's been working, and when it's done.

---

## Features

| Feature | What it does |
|---------|-------------|
| 🔴 **Real-time Progress** | Every tool call (Read/Edit/Bash/Write...) tracked and reported |
| ✅ **Instant Completion** | Stop hook fires the moment CC finishes — zero delay |
| 📊 **Tool Breakdown** | `{Read: 5, Edit: 6, Bash: 1}` — structured, queryable |
| 🔄 **Session Resume** | Get `session_id`, resume from where CC left off after timeout |
| 📁 **Atomic Files** | Progress written to disk safely, persists after completion |
| 🐍 **Python API** | `await run_task(prompt, cwd, max_turns)` — async, streaming |
| ⚡ **Dual Mode** | Works with both `claude -p` (CLI) and `claude-code-sdk` (Python) |

---

## Architecture

```
┌─────────────────┐          hook events           ┌───────────────────┐
│  Claude Code    │ ──────────────────────────────→ │  hermes_hook.py   │
│  (SDK or CLI)   │   PostToolUse / Stop / Notify   │  writes JSON to   │
└─────────────────┘                                 │  /tmp/cc-bridge/  │
         │                                          └───────────────────┘
         │ SDK stream                                           │
         ▼                                                      ▼
┌─────────────────┐                                 ┌───────────────────┐
│   cc_sdk.py     │ ←────────────────────────────── │  status files +   │
│   reads hooks   │         polls every 0.5s        │  progress file    │
│   + SDK stream  │                                 └───────────────────┘
└─────────────────┘
         │
         ▼
   JSON result with:
   • text output
   • tool_uses array
   • progress report (tool breakdown, elapsed, completed)
   • session_id (for resume)
```

**How it works:**

1. CC fires a `PostToolUse` hook every time it uses a tool → `hermes_hook.py` writes a JSON status file
2. `hermes_hook.py` also updates a unified `progress.json` on each tool call
3. CC fires a `Stop` hook when it finishes → `hermes_hook.py` writes the completion file with full stats
4. `cc_sdk.py` polls these files every 0.5s, builds structured progress reports
5. You query progress with `--progress <session_id>` or get it in the JSON result

---

## Quick Start

```bash
# 1. Install dependencies
pip3 install claude-code-sdk
npm install -g @anthropic-ai/claude-code

# 2. Clone & deploy scripts
git clone https://github.com/Evilom/hermes-cc-bridge.git
cd hermes-cc-bridge
cp scripts/cc_sdk.py scripts/hermes_hook.py ~/.local/bin/

# 3. Configure hooks (add to ~/.claude/settings.json)
#    See examples/settings.json for the template

# 4. Run a task
python3 ~/.local/bin/cc_sdk.py "Fix the auth bug" \
  --cwd /your/project --max-turns 8 --timeout 180 --json

# 5. Query progress (from another terminal)
python3 ~/.local/bin/cc_sdk.py --progress <session_id>
```

### Use Case 1: Don't Wait Blindly

```bash
# Fire and forget
python3 cc_sdk.py "Refactor auth module" --max-turns 15 --timeout 600 --json &

# Check progress anytime
python3 cc_sdk.py --progress <session_id>
# → {"total_tool_calls": 8, "tool_breakdown": {"Read":3, "Edit":4, "Bash":1}, "elapsed": 32.1}
```

### Use Case 2: Don't Lose Work on Timeout

```bash
# First run — times out
RESULT=$(python3 cc_sdk.py "Complex task" --max-turns 10 --json)
SESSION_ID=$(echo "$RESULT" | python3 -c "import json,sys; print(json.load(sys.stdin)['session_id'])")

# Before: retry from scratch (wasting all previous work)
# After: resume from where CC left off
python3 cc_sdk.py "Continue" --resume "$SESSION_ID" --max-turns 5 --json
```

### Use Case 3: Python Integration

```python
import asyncio
from cc_sdk import run_task

async def deploy_check():
    result = await run_task(
        prompt="Run all tests and fix failures",
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
| `--bare` | off | ⚠️ Faster startup but disables hooks |

## Status Files

Location: `/tmp/cc-bridge-status/` (configurable via `CC_BRIDGE_STATUS_DIR` env var)

| Pattern | When | Purpose |
|---------|------|---------|
| `{id}.json` | Stop | Completion signal with full stats |
| `{id}-PostToolUse-{ts}.json` | Each tool call | Individual tool record |
| `{id}-progress.json` | Each PostToolUse | Unified progress snapshot |
| `{id}-Notification-{ts}.json` | CC needs attention | Permission / input prompt |

## Key Design Decisions

1. **Stop event = completion signal.** Not timeout. Not max_turns. The hook fires the instant CC finishes.
2. **Progress files are atomic.** Write to `.tmp`, then rename. No partial reads.
3. **Progress persists after completion.** Post-mortem analysis without re-running.
4. **PostToolUse and Stop use separate files.** Progress events can't clobber the completion signal.

## Requirements

- Python 3.10+
- `claude-code-sdk` (`pip3 install claude-code-sdk`)
- Claude Code CLI (`npm install -g @anthropic-ai/claude-code`)

## License

MIT
