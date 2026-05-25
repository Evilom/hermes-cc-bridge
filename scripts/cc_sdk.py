     1|#!/usr/bin/env python3
     2|"""
     3|Claude Code SDK Bridge v3.1 — Hermes ↔ Claude Code 稳定调用桥
     4|基于 Claude Code v2.1.150 + claude-code-sdk v0.0.25 + Hermes Hooks Plugin
     5|
     6|用法:
     7|    python3 cc_sdk.py "任务" --cwd /path --max-turns 3 --tools Read,Write
     8|    python3 cc_sdk.py "任务" --bare --timeout 30
     9|    python3 cc_sdk.py "继续" --resume <session_id> --max-turns 5
    10|
    11|v3.0 更新:
    12|    - Hook 驱动完成检测：监听 CC 的 Stop 事件，不再依赖 timeout 猜测
    13|    - 实时进度追踪：PostToolUse 事件报告当前正在用什么工具
    14|    - 超时后自动提取已完成的结果（即使进程被杀）
    15|    - --no-hooks: 禁用 hook 监听（向后兼容）
    16|"""
    17|
    18|import asyncio
    19|import argparse
    20|import json
    21|import sys
    22|import time
    23|import os
    24|import glob
    25|from pathlib import Path
    26|
    27|try:
    28|    from claude_code_sdk import query, ClaudeCodeOptions
    29|except ImportError:
    30|    print("ERROR: claude-code-sdk not installed. Run: pip3 install claude-code-sdk", file=sys.stderr)
    31|    sys.exit(1)
    32|
    33|STATUS_DIR = Path(os.environ.get("CC_BRIDGE_STATUS_DIR", "/tmp/cc-bridge-status"))
    34|
    35|
    36|def find_status_file(session_id: str = None) -> Path | None:
    37|    """查找状态文件"""
    38|    if session_id:
    39|        safe_id = session_id.replace("/", "_")[:64]
    40|        p = STATUS_DIR / f"{safe_id}.json"
    41|        if p.exists():
    42|            return p
    43|    
    44|    # 查找最新的状态文件
    45|    files = sorted(STATUS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    46|    return files[0] if files else None
    47|
    48|
    49|def read_status(status_file: Path) -> dict | None:
    50|    """读取状态文件"""
    51|    try:
    52|        with open(status_file) as f:
    53|            return json.load(f)
    54|    except (FileNotFoundError, json.JSONDecodeError):
    55|        return None
    56|
    57|
    58|def cleanup_status(session_id: str):
    59|    """清理状态文件"""
    60|    if session_id:
    61|        safe_id = session_id.replace("/", "_")[:64]
    62|        p = STATUS_DIR / f"{safe_id}.json"
    63|        p.unlink(missing_ok=True)
    64|
    65|
    66|def build_progress_report(session_id: str, start_time: float = None) -> dict:
    67|    """
    68|    扫描所有 PostToolUse 事件文件，构建结构化进度报告。
    69|    可被外部轮询调用（--progress 模式）。
    70|    start_time 为 None 时从最早事件推算。
    71|    """
    72|    if not session_id:
    73|        return {"error": "no session_id"}
    74|    
    75|    safe_id = session_id.replace("/", "_")[:64]
    76|    
    77|    tool_counts = {}
    78|    tool_timeline = []
    79|    total_tools = 0
    80|    earliest_ts = None
    81|    
    82|    # 扫描所有 PostToolUse 事件
    83|    for f in sorted(STATUS_DIR.glob(f"{safe_id}-PostToolUse-*.json")):
    84|        status = read_status(f)
    85|        if not status:
    86|            continue
    87|        ts = status.get("timestamp", 0)
    88|        if earliest_ts is None or ts < earliest_ts:
    89|            earliest_ts = ts
    90|        data = status.get("data", {})
    91|        tool_name = data.get("tool_name", "?")
    92|        tool_counts[tool_name] = tool_counts.get(tool_name, 0) + 1
    93|        total_tools += 1
    94|        tool_timeline.append({
    95|            "tool": tool_name,
    96|            "ts": ts,
    97|            "input": data.get("tool_input", "")[:80],
    98|        })
    99|    
   100|    # 推算 start_time
   101|    if start_time is None:
   102|        start_time = earliest_ts if earliest_ts else time.time()
   103|    elapsed = time.time() - start_time
   104|    
   105|    # 检查是否已完成（Stop 事件）
   106|    stop_file = STATUS_DIR / f"{safe_id}.json"
   107|    completed = False
   108|    stop_stats = {}
   109|    if stop_file.exists():
   110|        stop_status = read_status(stop_file)
   111|        if stop_status and stop_status.get("event") == "Stop":
   112|            completed = True
   113|            stop_stats = stop_status.get("data", {}).get("stats", {})
   114|    
   115|    # 写入进度文件供外部轮询
   116|    progress = {
   117|        "session_id": session_id,
   118|        "elapsed": round(elapsed, 1),
   119|        "completed": completed,
   120|        "total_tool_calls": total_tools,
   121|        "tool_breakdown": tool_counts,
   122|        "last_tool": tool_timeline[-1] if tool_timeline else None,
   123|        "stop_stats": stop_stats,
   124|    }
   125|    
   126|    progress_file = STATUS_DIR / f"{safe_id}-progress.json"
   127|    tmp = progress_file.with_suffix(".tmp")
   128|    with open(tmp, "w") as f:
   129|        json.dump(progress, f, ensure_ascii=False, indent=2)
   130|    tmp.rename(progress_file)
   131|    
   132|    return progress
   133|
   134|
   135|async def wait_for_stop_event(
   136|    timeout: float,
   137|    poll_interval: float = 0.5,
   138|    session_id: str = None,
   139|    quiet: bool = False,
   140|    progress_callback=None,
   141|) -> dict | None:
   142|    """
   143|    等待 CC 的 Stop 事件（主完成信号）。
   144|    实时追踪所有 PostToolUse 事件，构建结构化进度报告。
   145|    每次发现新事件时更新进度文件 + 调用回调。
   146|    """
   147|    start = time.time()
   148|    seen_events = set()  # 已处理的事件文件名
   149|    last_report = None
   150|    
   151|    # 清理旧的状态文件
   152|    if session_id:
   153|        cleanup_status(session_id)
   154|    
   155|    while time.time() - start < timeout:
   156|        # 检查 Stop 事件（完成信号）
   157|        if session_id:
   158|            safe_id = session_id.replace("/", "_")[:64]
   159|            stop_file = STATUS_DIR / f"{safe_id}.json"
   160|            if stop_file.exists():
   161|                status = read_status(stop_file)
   162|                if status and status.get("event") == "Stop":
   163|                    elapsed = time.time() - start
   164|                    stats = status.get("data", {}).get("stats", {})
   165|                    if not quiet:
   166|                        tools = ", ".join(stats.get("tools_used", []))
   167|                        t = stats.get("total_turns", "?")
   168|                        tc = stats.get("total_tool_calls", "?")
   169|                        print(f"\n[CC Progress] ✅ 完成! {elapsed:.0f}s | turns={t} | tools={tc} | used: {tools}", 
   170|                              file=sys.stderr, flush=True)
   171|                    # 最终进度报告
   172|                    report = build_progress_report(session_id, start)
   173|                    if progress_callback:
   174|                        progress_callback(report)
   175|                    return status
   176|        
   177|        # 扫描新的 PostToolUse 事件
   178|        if session_id:
   179|            safe_id = session_id.replace("/", "_")[:64]
   180|            new_events = False
   181|            for f in sorted(STATUS_DIR.glob(f"{safe_id}-PostToolUse-*.json")):
   182|                fname = f.name
   183|                if fname not in seen_events:
   184|                    seen_events.add(fname)
   185|                    new_events = True
   186|            
   187|            if new_events:
   188|                report = build_progress_report(session_id, start)
   189|                last_report = report
   190|                if not quiet:
   191|                    tc = report["total_tool_calls"]
   192|                    lt = report.get("last_tool", {}).get("tool", "?")
   193|                    elapsed = report["elapsed"]
   194|                    # 只在工具切换或每5次时打印
   195|                    if tc <= 1 or tc % 5 == 0 or (report.get("last_tool", {}).get("tool") != 
   196|                        (last_report or {}).get("last_tool", {}).get("tool")):
   197|                        bd = report['tool_breakdown']
   198|                        print(f"\n[CC Progress] {elapsed:.0f}s | tool #{tc}: {lt} | breakdown: {bd}", 
   199|                              file=sys.stderr, flush=True)
   200|                if progress_callback:
   201|                    progress_callback(report)
   202|        
   203|        await asyncio.sleep(poll_interval)
   204|    
   205|    return None
   206|
   207|
   208|async def run_task(
   209|    prompt: str,
   210|    cwd: str = ".",
   211|    max_turns: int = 5,
   212|    allowed_tools: list[str] | None = None,
   213|    effort: str = "medium",
   214|    timeout: int = 180,
   215|    output_json: bool = False,
   216|    model: str | None = None,
   217|    system_prompt: str | None = None,
   218|    append_system_prompt: str | None = None,
   219|    append_system_prompt_file: str | None = None,
   220|    bare: bool = False,
   221|    resume: str | None = None,
   222|    continue_conversation: bool = False,
   223|    mcp_config: str | None = None,
   224|    disallowed_tools: list[str] | None = None,
   225|    env: dict[str, str] | None = None,
   226|    quiet: bool = False,
   227|    use_hooks: bool = True,
   228|) -> dict:
   229|    """Run a Claude Code task via SDK, return structured result."""
   230|    start_time = time.time()
   231|
   232|    opts = {
   233|        "max_turns": max_turns,
   234|        "cwd": os.path.abspath(cwd),
   235|        "permission_mode": "bypassPermissions",
   236|    }
   237|
   238|    if allowed_tools:
   239|        opts["allowed_tools"] = allowed_tools
   240|    if disallowed_tools:
   241|        opts["disallowed_tools"] = disallowed_tools
   242|    if model:
   243|        opts["model"] = model
   244|    if resume:
   245|        opts["resume"] = resume
   246|        opts.pop("max_turns", None)
   247|    if continue_conversation:
   248|        opts["continue_conversation"] = True
   249|        opts.pop("max_turns", None)
   250|    if env:
   251|        opts["env"] = env
   252|
   253|    # Bare 模式
   254|    if bare:
   255|        env_dict = opts.get("env", {})
   256|        env_dict["CLAUDE_CODE_SIMPLE"] = "1"
   257|        opts["env"] = env_dict
   258|
   259|    # MCP config
   260|    extra_args = {}
   261|    if mcp_config:
   262|        extra_args["mcp-config"] = mcp_config
   263|    if extra_args:
   264|        opts["extra_args"] = extra_args
   265|
   266|    # System prompt
   267|    if system_prompt:
   268|        opts["system_prompt"] = system_prompt
   269|    if append_system_prompt:
   270|        opts["append_system_prompt"] = append_system_prompt
   271|    if append_system_prompt_file and os.path.exists(append_system_prompt_file):
   272|        with open(append_system_prompt_file) as f:
   273|            file_content = f.read()
   274|        existing = opts.get("append_system_prompt", "")
   275|        opts["append_system_prompt"] = (existing + "\n" + file_content).strip() if existing else file_content
   276|
   277|    # Effort
   278|    effort_map = {
   279|        "low": "Be concise. Quick answers only.",
   280|        "medium": "",
   281|        "high": "Think carefully and thoroughly before acting.",
   282|        "max": "Use ultrathink: deeply reason about every aspect before any action.",
   283|    }
   284|    if effort in effort_map and effort_map[effort]:
   285|        extra = effort_map[effort]
   286|        existing = opts.get("append_system_prompt", "")
   287|        opts["append_system_prompt"] = (existing + "\n" + extra).strip() if existing else extra
   288|
   289|    options = ClaudeCodeOptions(**opts)
   290|
   291|    texts = []
   292|    tool_uses = []
   293|    errors = []
   294|    message_count = 0
   295|    last_text = ""
   296|    result_message = None
   297|    session_id = None
   298|
   299|    # 同时运行 SDK 查询和 Hook 监听
   300|    async def sdk_loop():
   301|        nonlocal last_text, result_message, message_count, session_id
   302|        try:
   303|            async for msg in query(prompt=prompt, options=options):
   304|                message_count += 1
   305|
   306|                if hasattr(msg, "content") and isinstance(msg.content, list):
   307|                    for block in msg.content:
   308|                        if hasattr(block, "text"):
   309|                            texts.append(block.text)
   310|                            last_text = block.text
   311|                        elif hasattr(block, "type") and block.type == "tool_use":
   312|                            tool_uses.append({
   313|                                "tool": getattr(block, "name", "unknown"),
   314|                                "input_summary": str(getattr(block, "input", {}))[:100],
   315|                            })
   316|
   317|                if hasattr(msg, "subtype"):
   318|                    result_message = msg
   319|                    subtype = msg.subtype
   320|                    if "error" in subtype:
   321|                        errors.append(f"CC error: {subtype}")
   322|                    if hasattr(msg, "session_id"):
   323|                        session_id = msg.session_id
   324|
   325|                # Stream to stderr
   326|                if not quiet and not output_json and hasattr(msg, "content"):
   327|                    for block in (msg.content if isinstance(msg.content, list) else []):
   328|                        if hasattr(block, "text") and block.text:
   329|                            print(block.text, end="", file=sys.stderr, flush=True)
   330|        except Exception as e:
   331|            errors.append(f"SDK EXCEPTION: {str(e)}")
   332|
   333|    async def hook_monitor():
   334|        """监听 Hook 事件"""
   335|        if not use_hooks:
   336|            return None
   337|        return await wait_for_stop_event(
   338|            timeout=timeout + 10,  # 比 SDK 多等 10s
   339|            session_id=session_id,
   340|            quiet=quiet or output_json,
   341|        )
   342|
   343|    # 并行运行
   344|    try:
   345|        async with asyncio.timeout(timeout + 5):
   346|            sdk_task = asyncio.create_task(sdk_loop())
   347|            hook_task = asyncio.create_task(hook_monitor())
   348|            
   349|            # 等待 SDK 完成（主要路径）
   350|            await sdk_task
   351|            
   352|            # 取消 hook 监听（SDK 已完成）
   353|            hook_task.cancel()
   354|            try:
   355|                await hook_task
   356|            except asyncio.CancelledError:
   357|                pass
   358|                
   359|    except asyncio.TimeoutError:
   360|        errors.append(f"TIMEOUT after {timeout}s")
   361|        # 尝试从 hook 状态文件获取结果
   362|        hook_status = find_status_file(session_id)
   363|        if hook_status:
   364|            status = read_status(hook_status)
   365|            if status and status.get("event") == "Stop":
   366|                errors = [e for e in errors if "TIMEOUT" not in e]
   367|                errors.append(f"Recovered from hook after timeout")
   368|                stats = status.get("data", {}).get("stats", {})
   369|                if not last_text and status.get("data", {}).get("last_message"):
   370|                    last_text = status["data"]["last_message"]
   371|                    texts.append(last_text)
   372|        # 即使超时也构建进度报告
   373|        if session_id:
   374|            build_progress_report(session_id, start_time)
   375|
   376|    elapsed = time.time() - start_time
   377|
   378|    # 提取 session_id
   379|    if result_message and hasattr(result_message, "session_id"):
   380|        session_id = result_message.session_id
   381|
   382|    # 构建最终进度报告（含 hook 数据）
   383|    progress = {}
   384|    if session_id:
   385|        progress = build_progress_report(session_id, start_time)
   386|
   387|    result = {
   388|        "success": len(errors) == 0,
   389|        "text": last_text,
   390|        "full_text": "\n".join(texts),
   391|        "tool_uses": tool_uses,
   392|        "tool_count": len(tool_uses),
   393|        "message_count": message_count,
   394|        "elapsed_seconds": round(elapsed, 1),
   395|        "errors": errors,
   396|        "session_id": session_id,
   397|        "progress": progress,
   398|    }
   399|
   400|    return result
   401|
   402|
   403|def main():
   404|    parser = argparse.ArgumentParser(description="Claude Code SDK Bridge v3.1 for Hermes")
   405|    parser.add_argument("prompt", nargs="?", default=None, help="Task prompt for Claude Code (optional with --progress)")
   406|    parser.add_argument("--cwd", default=".", help="Working directory (default: .)")
   407|    parser.add_argument("--max-turns", type=int, default=5, help="Max agentic turns (default: 5)")
   408|    parser.add_argument("--tools", default=None, help="Allowed tools, comma-separated (default: all)")
   409|    parser.add_argument("--disallowed-tools", default=None, help="Disallowed tools, comma-separated")
   410|    parser.add_argument("--effort", default="medium", choices=["low", "medium", "high", "max"],
   411|                        help="Reasoning effort (default: medium)")
   412|    parser.add_argument("--timeout", type=int, default=180, help="Timeout in seconds (default: 180)")
   413|    parser.add_argument("--json", action="store_true", help="Output JSON result")
   414|    parser.add_argument("--model", default=None, help="Model override")
   415|    parser.add_argument("--system-prompt", default=None, help="Custom system prompt (replaces default)")
   416|    parser.add_argument("--append-system-prompt", default=None, help="Append to system prompt")
   417|    parser.add_argument("--append-system-prompt-file", default=None, help="Append file to system prompt")
   418|    parser.add_argument("--bare", action="store_true", help="Bare mode: faster startup (WARNING: disables hooks!)")
   419|    parser.add_argument("--resume", default=None, help="Resume a session by ID")
   420|    parser.add_argument("--continue", dest="continue_conversation", action="store_true",
   421|                        help="Continue the most recent session in this directory")
   422|    parser.add_argument("--mcp-config", default=None, help="Path to MCP config JSON")
   423|    parser.add_argument("--env", default=None, help="Environment variables as JSON string")
   424|    parser.add_argument("--quiet", action="store_true", help="No streaming output to stderr")
   425|    parser.add_argument("--no-hooks", action="store_true", help="Disable hook-based completion detection")
   426|    parser.add_argument("--progress", default=None, metavar="SESSION_ID",
   427|                        help="Query live progress of a running CC session (read-only, exits immediately)")
   428|
   429|    args = parser.parse_args()
   430|
   431|    # --progress 模式：查询进度并退出
   432|    if args.progress:
   433|        report = build_progress_report(args.progress)
   434|        print(json.dumps(report, ensure_ascii=False, indent=2))
   435|        sys.exit(0)
   436|
   437|    if not args.prompt:
   438|        parser.error("prompt is required (unless using --progress)")
   439|
   440|    # bare 模式警告
   441|    if args.bare and not args.no_hooks:
   442|        print("[cc_sdk] WARNING: --bare disables hooks! Use --no-hooks to silence this.", file=sys.stderr)
   443|
   444|    allowed_tools = args.tools.split(",") if args.tools else None
   445|    disallowed_tools = args.disallowed_tools.split(",") if args.disallowed_tools else None
   446|    env = json.loads(args.env) if args.env else None
   447|
   448|    result = asyncio.run(run_task(
   449|        prompt=args.prompt,
   450|        cwd=args.cwd,
   451|        max_turns=args.max_turns,
   452|        allowed_tools=allowed_tools,
   453|        disallowed_tools=disallowed_tools,
   454|        effort=args.effort,
   455|        timeout=args.timeout,
   456|        output_json=args.json or args.quiet,
   457|        model=args.model,
   458|        system_prompt=args.system_prompt,
   459|        append_system_prompt=args.append_system_prompt,
   460|        append_system_prompt_file=args.append_system_prompt_file,
   461|        bare=args.bare,
   462|        resume=args.resume,
   463|        continue_conversation=args.continue_conversation,
   464|        mcp_config=args.mcp_config,
   465|        env=env,
   466|        quiet=args.quiet,
   467|        use_hooks=not args.no_hooks,
   468|    ))
   469|
   470|    if args.json:
   471|        print(json.dumps(result, ensure_ascii=False, indent=2))
   472|    else:
   473|        if result["success"]:
   474|            print(result["text"])
   475|        else:
   476|            print(f"FAILED: {'; '.join(result['errors'])}", file=sys.stderr)
   477|            if result["text"]:
   478|                print(result["text"])
   479|            if result.get("session_id"):
   480|                print(f"\nResume with: --resume {result['session_id']}", file=sys.stderr)
   481|
   482|    sys.exit(0 if result["success"] else 1)
   483|
   484|
   485|if __name__ == "__main__":
   486|    main()
   487|