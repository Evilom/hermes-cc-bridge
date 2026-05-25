#!/usr/bin/env python3
"""
Claude Code SDK Bridge v3.1 — Hermes ↔ Claude Code 稳定调用桥
基于 Claude Code v2.1.150 + claude-code-sdk v0.0.25 + Hermes Hooks Plugin

用法:
    python3 cc_sdk.py "任务" --cwd /path --max-turns 3 --tools Read,Write
    python3 cc_sdk.py "任务" --bare --timeout 30
    python3 cc_sdk.py "继续" --resume <session_id> --max-turns 5

v3.0 更新:
    - Hook 驱动完成检测：监听 CC 的 Stop 事件，不再依赖 timeout 猜测
    - 实时进度追踪：PostToolUse 事件报告当前正在用什么工具
    - 超时后自动提取已完成的结果（即使进程被杀）
    - --no-hooks: 禁用 hook 监听（向后兼容）
"""

import asyncio
import argparse
import json
import sys
import time
import os
import glob
from pathlib import Path

try:
    from claude_code_sdk import query, ClaudeCodeOptions
except ImportError:
    print("ERROR: claude-code-sdk not installed. Run: pip3 install claude-code-sdk", file=sys.stderr)
    sys.exit(1)

STATUS_DIR = Path("/tmp/hermes-cc-status")


def find_status_file(session_id: str = None) -> Path | None:
    """查找状态文件"""
    if session_id:
        safe_id = session_id.replace("/", "_")[:64]
        p = STATUS_DIR / f"{safe_id}.json"
        if p.exists():
            return p
    
    # 查找最新的状态文件
    files = sorted(STATUS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def read_status(status_file: Path) -> dict | None:
    """读取状态文件"""
    try:
        with open(status_file) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def cleanup_status(session_id: str):
    """清理状态文件"""
    if session_id:
        safe_id = session_id.replace("/", "_")[:64]
        p = STATUS_DIR / f"{safe_id}.json"
        p.unlink(missing_ok=True)


def build_progress_report(session_id: str, start_time: float = None) -> dict:
    """
    扫描所有 PostToolUse 事件文件，构建结构化进度报告。
    可被外部轮询调用（--progress 模式）。
    start_time 为 None 时从最早事件推算。
    """
    if not session_id:
        return {"error": "no session_id"}
    
    safe_id = session_id.replace("/", "_")[:64]
    
    tool_counts = {}
    tool_timeline = []
    total_tools = 0
    earliest_ts = None
    
    # 扫描所有 PostToolUse 事件
    for f in sorted(STATUS_DIR.glob(f"{safe_id}-PostToolUse-*.json")):
        status = read_status(f)
        if not status:
            continue
        ts = status.get("timestamp", 0)
        if earliest_ts is None or ts < earliest_ts:
            earliest_ts = ts
        data = status.get("data", {})
        tool_name = data.get("tool_name", "?")
        tool_counts[tool_name] = tool_counts.get(tool_name, 0) + 1
        total_tools += 1
        tool_timeline.append({
            "tool": tool_name,
            "ts": ts,
            "input": data.get("tool_input", "")[:80],
        })
    
    # 推算 start_time
    if start_time is None:
        start_time = earliest_ts if earliest_ts else time.time()
    elapsed = time.time() - start_time
    
    # 检查是否已完成（Stop 事件）
    stop_file = STATUS_DIR / f"{safe_id}.json"
    completed = False
    stop_stats = {}
    if stop_file.exists():
        stop_status = read_status(stop_file)
        if stop_status and stop_status.get("event") == "Stop":
            completed = True
            stop_stats = stop_status.get("data", {}).get("stats", {})
    
    # 写入进度文件供外部轮询
    progress = {
        "session_id": session_id,
        "elapsed": round(elapsed, 1),
        "completed": completed,
        "total_tool_calls": total_tools,
        "tool_breakdown": tool_counts,
        "last_tool": tool_timeline[-1] if tool_timeline else None,
        "stop_stats": stop_stats,
    }
    
    progress_file = STATUS_DIR / f"{safe_id}-progress.json"
    tmp = progress_file.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)
    tmp.rename(progress_file)
    
    return progress


async def wait_for_stop_event(
    timeout: float,
    poll_interval: float = 0.5,
    session_id: str = None,
    quiet: bool = False,
    progress_callback=None,
) -> dict | None:
    """
    等待 CC 的 Stop 事件（主完成信号）。
    实时追踪所有 PostToolUse 事件，构建结构化进度报告。
    每次发现新事件时更新进度文件 + 调用回调。
    """
    start = time.time()
    seen_events = set()  # 已处理的事件文件名
    last_report = None
    
    # 清理旧的状态文件
    if session_id:
        cleanup_status(session_id)
    
    while time.time() - start < timeout:
        # 检查 Stop 事件（完成信号）
        if session_id:
            safe_id = session_id.replace("/", "_")[:64]
            stop_file = STATUS_DIR / f"{safe_id}.json"
            if stop_file.exists():
                status = read_status(stop_file)
                if status and status.get("event") == "Stop":
                    elapsed = time.time() - start
                    stats = status.get("data", {}).get("stats", {})
                    if not quiet:
                        tools = ", ".join(stats.get("tools_used", []))
                        t = stats.get("total_turns", "?")
                        tc = stats.get("total_tool_calls", "?")
                        print(f"\n[CC Progress] ✅ 完成! {elapsed:.0f}s | turns={t} | tools={tc} | used: {tools}", 
                              file=sys.stderr, flush=True)
                    # 最终进度报告
                    report = build_progress_report(session_id, start)
                    if progress_callback:
                        progress_callback(report)
                    return status
        
        # 扫描新的 PostToolUse 事件
        if session_id:
            safe_id = session_id.replace("/", "_")[:64]
            new_events = False
            for f in sorted(STATUS_DIR.glob(f"{safe_id}-PostToolUse-*.json")):
                fname = f.name
                if fname not in seen_events:
                    seen_events.add(fname)
                    new_events = True
            
            if new_events:
                report = build_progress_report(session_id, start)
                last_report = report
                if not quiet:
                    tc = report["total_tool_calls"]
                    lt = report.get("last_tool", {}).get("tool", "?")
                    elapsed = report["elapsed"]
                    # 只在工具切换或每5次时打印
                    if tc <= 1 or tc % 5 == 0 or (report.get("last_tool", {}).get("tool") != 
                        (last_report or {}).get("last_tool", {}).get("tool")):
                        bd = report['tool_breakdown']
                        print(f"\n[CC Progress] {elapsed:.0f}s | tool #{tc}: {lt} | breakdown: {bd}", 
                              file=sys.stderr, flush=True)
                if progress_callback:
                    progress_callback(report)
        
        await asyncio.sleep(poll_interval)
    
    return None


async def run_task(
    prompt: str,
    cwd: str = ".",
    max_turns: int = 5,
    allowed_tools: list[str] | None = None,
    effort: str = "medium",
    timeout: int = 180,
    output_json: bool = False,
    model: str | None = None,
    system_prompt: str | None = None,
    append_system_prompt: str | None = None,
    append_system_prompt_file: str | None = None,
    bare: bool = False,
    resume: str | None = None,
    continue_conversation: bool = False,
    mcp_config: str | None = None,
    disallowed_tools: list[str] | None = None,
    env: dict[str, str] | None = None,
    quiet: bool = False,
    use_hooks: bool = True,
) -> dict:
    """Run a Claude Code task via SDK, return structured result."""
    start_time = time.time()

    opts = {
        "max_turns": max_turns,
        "cwd": os.path.abspath(cwd),
        "permission_mode": "bypassPermissions",
    }

    if allowed_tools:
        opts["allowed_tools"] = allowed_tools
    if disallowed_tools:
        opts["disallowed_tools"] = disallowed_tools
    if model:
        opts["model"] = model
    if resume:
        opts["resume"] = resume
        opts.pop("max_turns", None)
    if continue_conversation:
        opts["continue_conversation"] = True
        opts.pop("max_turns", None)
    if env:
        opts["env"] = env

    # Bare 模式
    if bare:
        env_dict = opts.get("env", {})
        env_dict["CLAUDE_CODE_SIMPLE"] = "1"
        opts["env"] = env_dict

    # MCP config
    extra_args = {}
    if mcp_config:
        extra_args["mcp-config"] = mcp_config
    if extra_args:
        opts["extra_args"] = extra_args

    # System prompt
    if system_prompt:
        opts["system_prompt"] = system_prompt
    if append_system_prompt:
        opts["append_system_prompt"] = append_system_prompt
    if append_system_prompt_file and os.path.exists(append_system_prompt_file):
        with open(append_system_prompt_file) as f:
            file_content = f.read()
        existing = opts.get("append_system_prompt", "")
        opts["append_system_prompt"] = (existing + "\n" + file_content).strip() if existing else file_content

    # Effort
    effort_map = {
        "low": "Be concise. Quick answers only.",
        "medium": "",
        "high": "Think carefully and thoroughly before acting.",
        "max": "Use ultrathink: deeply reason about every aspect before any action.",
    }
    if effort in effort_map and effort_map[effort]:
        extra = effort_map[effort]
        existing = opts.get("append_system_prompt", "")
        opts["append_system_prompt"] = (existing + "\n" + extra).strip() if existing else extra

    options = ClaudeCodeOptions(**opts)

    texts = []
    tool_uses = []
    errors = []
    message_count = 0
    last_text = ""
    result_message = None
    session_id = None

    # 同时运行 SDK 查询和 Hook 监听
    async def sdk_loop():
        nonlocal last_text, result_message, message_count, session_id
        try:
            async for msg in query(prompt=prompt, options=options):
                message_count += 1

                if hasattr(msg, "content") and isinstance(msg.content, list):
                    for block in msg.content:
                        if hasattr(block, "text"):
                            texts.append(block.text)
                            last_text = block.text
                        elif hasattr(block, "type") and block.type == "tool_use":
                            tool_uses.append({
                                "tool": getattr(block, "name", "unknown"),
                                "input_summary": str(getattr(block, "input", {}))[:100],
                            })

                if hasattr(msg, "subtype"):
                    result_message = msg
                    subtype = msg.subtype
                    if "error" in subtype:
                        errors.append(f"CC error: {subtype}")
                    if hasattr(msg, "session_id"):
                        session_id = msg.session_id

                # Stream to stderr
                if not quiet and not output_json and hasattr(msg, "content"):
                    for block in (msg.content if isinstance(msg.content, list) else []):
                        if hasattr(block, "text") and block.text:
                            print(block.text, end="", file=sys.stderr, flush=True)
        except Exception as e:
            errors.append(f"SDK EXCEPTION: {str(e)}")
            # SDK 崩溃时尝试从 hook 状态文件恢复已完成的工作
            _recover_from_hooks()

    def _recover_from_hooks():
        """从 hook 状态文件恢复：Stop 事件 + session_id + 最后消息 + 工具使用"""
        nonlocal last_text, session_id
        # 尝试所有已知的 session_id
        candidate_ids = set()
        if session_id:
            candidate_ids.add(session_id)
        if result_message and hasattr(result_message, "session_id"):
            candidate_ids.add(result_message.session_id)
        # 从 hook 状态目录扫描最近的文件
        for f in sorted(STATUS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:5]:
            status = read_status(f)
            if status and status.get("session_id"):
                candidate_ids.add(status["session_id"])

        for sid in candidate_ids:
            safe_id = sid.replace("/", "_")[:64]
            stop_file = STATUS_DIR / f"{safe_id}.json"

            # 路径 1: 有 Stop 事件（CC 完成了但 SDK 没收到结果）
            if stop_file.exists():
                status = read_status(stop_file)
                if status and status.get("event") == "Stop":
                    data = status.get("data", {})
                    stats = data.get("stats", {})
                    if not last_text and data.get("last_message"):
                        last_text = data["last_message"]
                        texts.append(last_text)
                    if not session_id:
                        session_id = sid
                    errors.append(f"Recovered from hook after SDK crash (tools={stats.get('total_tool_calls', '?')})")
                    return

            # 路径 2: 没有 Stop 但有 PostToolUse（CC 做了一部分工作后崩溃）
            tool_files = sorted(STATUS_DIR.glob(f"{safe_id}-PostToolUse-*.json"))
            if tool_files and not tool_uses:
                for tf in tool_files:
                    ts = read_status(tf)
                    if ts:
                        tool_name = ts.get("data", {}).get("tool_name", "?")
                        tool_input = ts.get("data", {}).get("tool_input", "")[:100]
                        tool_uses.append({"tool": tool_name, "input_summary": tool_input})
                if not session_id:
                    session_id = sid
                errors.append(f"Partial recovery from hooks: {len(tool_uses)} tool calls captured")
                return

    async def hook_monitor():
        """监听 Hook 事件"""
        if not use_hooks:
            return None
        return await wait_for_stop_event(
            timeout=timeout + 10,  # 比 SDK 多等 10s
            session_id=session_id,
            quiet=quiet or output_json,
        )

    # 并行运行
    try:
        async with asyncio.timeout(timeout + 5):
            sdk_task = asyncio.create_task(sdk_loop())
            hook_task = asyncio.create_task(hook_monitor())
            
            # 等待 SDK 完成（主要路径）
            await sdk_task
            
            # 取消 hook 监听（SDK 已完成）
            hook_task.cancel()
            try:
                await hook_task
            except asyncio.CancelledError:
                pass
                
    except asyncio.TimeoutError:
        errors.append(f"TIMEOUT after {timeout}s")
        # 尝试从 hook 状态文件获取结果
        hook_status = find_status_file(session_id)
        if hook_status:
            status = read_status(hook_status)
            if status and status.get("event") == "Stop":
                errors = [e for e in errors if "TIMEOUT" not in e]
                errors.append(f"Recovered from hook after timeout")
                stats = status.get("data", {}).get("stats", {})
                if not last_text and status.get("data", {}).get("last_message"):
                    last_text = status["data"]["last_message"]
                    texts.append(last_text)
        # 即使超时也构建进度报告
        if session_id:
            build_progress_report(session_id, start_time)

    elapsed = time.time() - start_time

    # 提取 session_id
    if result_message and hasattr(result_message, "session_id"):
        session_id = result_message.session_id

    # 构建最终进度报告（含 hook 数据）
    progress = {}
    if session_id:
        progress = build_progress_report(session_id, start_time)

    result = {
        "success": len(errors) == 0,
        "text": last_text,
        "full_text": "\n".join(texts),
        "tool_uses": tool_uses,
        "tool_count": len(tool_uses),
        "message_count": message_count,
        "elapsed_seconds": round(elapsed, 1),
        "errors": errors,
        "session_id": session_id,
        "progress": progress,
    }

    return result


def main():
    parser = argparse.ArgumentParser(description="Claude Code SDK Bridge v3.1 for Hermes")
    parser.add_argument("prompt", nargs="?", default=None, help="Task prompt for Claude Code (optional with --progress)")
    parser.add_argument("--cwd", default=".", help="Working directory (default: .)")
    parser.add_argument("--max-turns", type=int, default=5, help="Max agentic turns (default: 5)")
    parser.add_argument("--tools", default=None, help="Allowed tools, comma-separated (default: all)")
    parser.add_argument("--disallowed-tools", default=None, help="Disallowed tools, comma-separated")
    parser.add_argument("--effort", default="medium", choices=["low", "medium", "high", "max"],
                        help="Reasoning effort (default: medium)")
    parser.add_argument("--timeout", type=int, default=180, help="Timeout in seconds (default: 180)")
    parser.add_argument("--json", action="store_true", help="Output JSON result")
    parser.add_argument("--model", default=None, help="Model override")
    parser.add_argument("--system-prompt", default=None, help="Custom system prompt (replaces default)")
    parser.add_argument("--append-system-prompt", default=None, help="Append to system prompt")
    parser.add_argument("--append-system-prompt-file", default=None, help="Append file to system prompt")
    parser.add_argument("--bare", action="store_true", help="Bare mode: faster startup (WARNING: disables hooks!)")
    parser.add_argument("--resume", default=None, help="Resume a session by ID")
    parser.add_argument("--continue", dest="continue_conversation", action="store_true",
                        help="Continue the most recent session in this directory")
    parser.add_argument("--mcp-config", default=None, help="Path to MCP config JSON")
    parser.add_argument("--env", default=None, help="Environment variables as JSON string")
    parser.add_argument("--quiet", action="store_true", help="No streaming output to stderr")
    parser.add_argument("--no-hooks", action="store_true", help="Disable hook-based completion detection")
    parser.add_argument("--progress", default=None, metavar="SESSION_ID",
                        help="Query live progress of a running CC session (read-only, exits immediately)")

    args = parser.parse_args()

    # --progress 模式：查询进度并退出
    if args.progress:
        report = build_progress_report(args.progress)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        sys.exit(0)

    if not args.prompt:
        parser.error("prompt is required (unless using --progress)")

    # bare 模式警告
    if args.bare and not args.no_hooks:
        print("[cc_sdk] WARNING: --bare disables hooks! Use --no-hooks to silence this.", file=sys.stderr)

    allowed_tools = args.tools.split(",") if args.tools else None
    disallowed_tools = args.disallowed_tools.split(",") if args.disallowed_tools else None
    env = json.loads(args.env) if args.env else None

    result = asyncio.run(run_task(
        prompt=args.prompt,
        cwd=args.cwd,
        max_turns=args.max_turns,
        allowed_tools=allowed_tools,
        disallowed_tools=disallowed_tools,
        effort=args.effort,
        timeout=args.timeout,
        output_json=args.json or args.quiet,
        model=args.model,
        system_prompt=args.system_prompt,
        append_system_prompt=args.append_system_prompt,
        append_system_prompt_file=args.append_system_prompt_file,
        bare=args.bare,
        resume=args.resume,
        continue_conversation=args.continue_conversation,
        mcp_config=args.mcp_config,
        env=env,
        quiet=args.quiet,
        use_hooks=not args.no_hooks,
    ))

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if result["success"]:
            print(result["text"])
        else:
            print(f"FAILED: {'; '.join(result['errors'])}", file=sys.stderr)
            if result["text"]:
                print(result["text"])
            if result.get("session_id"):
                print(f"\nResume with: --resume {result['session_id']}", file=sys.stderr)

    sys.exit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()
