# hermes-cc-bridge

**Claude Code 实时进度追踪 & Hook 完成检测**

[English](README.md) | [中文](README_CN.md)

---

## 问题：Claude Code 是"盲"的

通过 SDK 给 Claude Code 派任务，你就像在闭眼开车：

**没有进度反馈。** 你发一个任务——"重构认证模块"——然后……什么都没有。CC 在读文件？在写代码？在跑测试？卡住了？你不知道。你盯着一个什么都不输出的终端，直到它要么成功，要么超时。

**Timeout 是你唯一的信号。** 所以你猜："这个任务大概要 300 秒吧。"如果 CC 30 秒就做完了，你白等 270 秒。如果 CC 需要 310 秒，你在 300 秒时杀了它——刚好在它完成之前。怎么都是亏。

**`max_turns` 没有意义。** 设 `max_turns=10` 不代表第 1 轮就是 10% 完成。CC 可能 3 轮就干完 80%，剩下 7 轮在做验证。也可能 10 轮全用完才做了一半。你看不出来。

**超时 ≠ 失败。** 这是最坑的。CC 超时的时候，它已经写好的文件还在磁盘上。活干完了——但你以为失败了，所以从头重跑，浪费时间和 token。我们见过任务在超时时已经 100% 完成，只是最后的验证步骤被杀了。

**没有会话连续性。** 跑完之后你不能问"刚才发生了什么"。没有结构化的输出——用了什么工具、改了什么文件、花了多久。只有一大段文字或者一个超时错误。

### 实际体验是这样的

```bash
# 以前：猜、等、碰运气
$ python3 cc_sdk.py "大重构" --max-turns 15 --timeout 600 --json

# ... 10 分钟的沉默 ...

# 结果要么是：
#   ✅ {"success": true, ...}  （但你等了 10 分钟，其实 2 分钟就做完了）
#   ❌ {"errors": ["TIMEOUT after 600s"]}  （但活其实干完了）
#   ❌ {"errors": ["Reached max turns"]}  （但 80% 已经完成了）
```

你不知道会得到哪种结果。失败的时候，你也不知道 CC 做到了哪一步。

---

## 解决方案：hermes-cc-bridge

**hermes-cc-bridge** 接入 Claude Code 的 Hook 事件系统，让你实时看到 CC 在做什么。

### 对比：以前 vs 现在

| | 以前（裸 SDK） | 现在（加 hermes-cc-bridge） |
|---|---|---|
| **进度** | 没有。等超时或成功。 | 实时。每个工具调用都看得见。 |
| **完成信号** | SDK 流结束或超时 | Stop hook 瞬间触发 |
| **"卡住了？"** | 不知道。等。 | 查 `--progress`——看实时工具数量和分布 |
| **超时失败** | 假设任务失败。从头重跑。 | 检查磁盘上的文件。需要的话用 `--resume` 继续。 |
| **CC 做了什么** | 一大段文字 | 结构化 JSON：用了什么工具、改了什么文件、花了多久 |
| **会话连续性** | 超时后丢失 | `session_id` 保留，从上次检查点继续 |

### 现在看到的是这样

```bash
$ python3 cc_sdk.py "大重构" --max-turns 15 --timeout 600 --json &

# 从另一个终端查进度：
$ python3 cc_sdk.py --progress <session_id>

# 输出：
[CC Progress] 12s | tool #3: Read | 分布: {Read: 3}
[CC Progress] 28s | tool #6: Edit | 分布: {Read: 4, Edit: 2}
[CC Progress] 45s | tool #12: Bash | 分布: {Read: 5, Edit: 6, Bash: 1}

# 完成时：
[CC Progress] ✅ 完成! 67s | 轮次=8 | 工具=14 | 使用: Read, Edit, Bash
```

不用猜了。CC 在做什么、做了多久、什么时候做完，一目了然。

---

## 核心功能

| 功能 | 说明 |
|------|------|
| 🔴 **实时进度** | 每个工具调用（Read/Edit/Bash/Write...）实时追踪和报告 |
| ✅ **即时完成** | Stop hook 在 CC 完成的瞬间触发，零延迟 |
| 📊 **工具统计** | `{Read: 5, Edit: 6, Bash: 1}`——结构化、可查询 |
| 🔄 **会话恢复** | 获取 `session_id`，超时后从上次中断处继续 |
| 📁 **原子写入** | 进度文件安全写入磁盘，完成后保留供事后分析 |
| 🐍 **Python API** | `await run_task(prompt, cwd, max_turns)`——异步流式 |
| ⚡ **双模式** | `claude -p`（CLI）和 `claude-code-sdk`（Python）都能用 |

---

## 架构

```
┌─────────────────┐          hook 事件             ┌───────────────────┐
│  Claude Code    │ ──────────────────────────────→ │  hermes_hook.py   │
│  (SDK 或 CLI)   │   PostToolUse / Stop / Notify   │  写入 JSON 到     │
└─────────────────┘                                 │  /tmp/cc-bridge/  │
         │                                          └───────────────────┘
         │ SDK 流                                           │
         ▼                                                  ▼
┌─────────────────┐                                 ┌───────────────────┐
│   cc_sdk.py     │ ←────────────────────────────── │  状态文件 +       │
│   读取 hooks    │         每 0.5s 轮询             │  进度文件         │
│   + SDK 流      │                                 └───────────────────┘
└─────────────────┘
         │
         ▼
   JSON 结果：
   • text 输出
   • tool_uses 数组
   • progress 报告（工具分布、耗时、完成状态）
   • session_id（用于 resume）
```

**工作原理：**

1. CC 每次使用工具时触发 `PostToolUse` hook → `hermes_hook.py` 写入 JSON 状态文件
2. `hermes_hook.py` 同时更新统一的 `progress.json`
3. CC 完成任务时触发 `Stop` hook → `hermes_hook.py` 写入完成文件（含完整统计）
4. `cc_sdk.py` 每 0.5 秒轮询这些文件，构建结构化进度报告
5. 你用 `--progress <session_id>` 查询进度，或在 JSON 结果中获取

---

## 快速开始

```bash
# 1. 安装依赖
pip3 install claude-code-sdk
npm install -g @anthropic-ai/claude-code

# 2. 克隆 & 部署脚本
git clone https://github.com/Evilom/hermes-cc-bridge.git
cd hermes-cc-bridge
cp scripts/cc_sdk.py scripts/hermes_hook.py ~/.local/bin/

# 3. 配置 hooks（加到 ~/.claude/settings.json）
#    参考 examples/settings.json 模板

# 4. 运行任务
python3 ~/.local/bin/cc_sdk.py "修复 auth bug" \
  --cwd /your/project --max-turns 8 --timeout 180 --json

# 5. 查询进度（另一个终端）
python3 ~/.local/bin/cc_sdk.py --progress <session_id>
```

### 场景 1：大任务不瞎等

```bash
# 发任务，后台跑
python3 cc_sdk.py "重构认证模块" --max-turns 15 --timeout 600 --json &

# 随时查进度
python3 cc_sdk.py --progress <session_id>
# → {"total_tool_calls": 8, "tool_breakdown": {"Read":3, "Edit":4, "Bash":1}, "elapsed": 32.1}
```

### 场景 2：超时不丢工作

```bash
# 第一次跑，超时了
RESULT=$(python3 cc_sdk.py "复杂任务" --max-turns 10 --json)
SESSION_ID=$(echo "$RESULT" | python3 -c "import json,sys; print(json.load(sys.stdin)['session_id'])")

# 以前：从头重跑（浪费之前所有工作）
# 现在：从上次中断处继续
python3 cc_sdk.py "继续" --resume "$SESSION_ID" --max-turns 5 --json
```

### 场景 3：Python 集成

```python
import asyncio
from cc_sdk import run_task

async def deploy_check():
    result = await run_task(
        prompt="跑所有测试，修失败的",
        cwd="/app",
        max_turns=12,
        timeout=300,
    )
    
    if result["success"]:
        tools = result["progress"]["tool_breakdown"]
        print(f"✅ 完成，耗时 {result['elapsed_seconds']}s，工具: {tools}")
    else:
        print(f"❌ 失败: {result['errors']}")

asyncio.run(deploy_check())
```

---

## CLI 参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--cwd` | `.` | 工作目录 |
| `--max-turns` | 5 | 最大 agentic 轮次 |
| `--timeout` | 180 | 超时秒数 |
| `--tools` | all | 允许的工具（逗号分隔） |
| `--effort` | medium | low / medium / high / max |
| `--json` | off | JSON 输出 |
| `--quiet` | off | 不输出到 stderr |
| `--resume` | none | 恢复指定会话 |
| `--continue` | off | 继续最近会话 |
| `--progress` | none | 查询实时进度（只读） |
| `--model` | none | 模型覆盖 |
| `--bare` | off | ⚠️ 更快但禁用 hooks |

## 状态文件

位置：`/tmp/cc-bridge-status/`（可通过 `CC_BRIDGE_STATUS_DIR` 环境变量配置）

| 文件模式 | 触发时机 | 用途 |
|---------|---------|------|
| `{id}.json` | Stop | 完成信号，含完整统计 |
| `{id}-PostToolUse-{ts}.json` | 每次工具调用 | 单次工具记录 |
| `{id}-progress.json` | 每次 PostToolUse | 统一进度快照 |
| `{id}-Notification-{ts}.json` | CC 等待注意 | 权限/输入提示 |

## 关键设计决策

1. **Stop 事件 = 完成信号。** 不是 timeout，不是 max_turns。Hook 在 CC 完成的瞬间触发。
2. **进度文件原子写入。** 先写 `.tmp`，再 rename。不会读到半写的内容。
3. **进度在完成后保留。** 不用重跑就能做事后分析。
4. **PostToolUse 和 Stop 用独立文件。** 进度事件不会覆盖完成信号。

## 环境要求

- Python 3.10+
- `claude-code-sdk`（`pip3 install claude-code-sdk`）
- Claude Code CLI（`npm install -g @anthropic-ai/claude-code`）

## 许可证

MIT
