     1|#!/usr/bin/env python3
     2|"""
     3|Hermes ↔ Claude Code Hook Bridge
     4|
     5|从 Claude Code 的 hook 事件中提取信息，写入状态文件供 cc_sdk.py 读取。
     6|支持事件：Stop, Notification, PostToolUse, SubagentStop, SessionStart
     7|
     8|状态文件：/tmp/hermes-cc-{session_id}.json
     9|格式：{"event": "Stop", "session_id": "...", "timestamp": ..., "data": {...}}
    10|"""
    11|
    12|import json
    13|import os
    14|import sys
    15|import time
    16|from pathlib import Path
    17|
    18|STATUS_DIR = Path(os.environ.get("CC_BRIDGE_STATUS_DIR", "/tmp/cc-bridge-status"))
    19|STATUS_DIR.mkdir(parents=True, exist_ok=True)
    20|
    21|def read_stdin_event() -> dict:
    22|    """从 stdin 读取 CC 传来的 hook 事件数据"""
    23|    try:
    24|        if sys.stdin.isatty():
    25|            return {}
    26|        raw = sys.stdin.read()
    27|        return json.loads(raw) if raw.strip() else {}
    28|    except (json.JSONDecodeError, IOError):
    29|        return {}
    30|
    31|def write_status(session_id: str, event_name: str, data: dict):
    32|    """写入状态文件。Stop 事件覆盖主文件，其他事件写独立文件。"""
    33|    if not session_id:
    34|        session_id = data.get("session_id", "unknown")
    35|    
    36|    safe_id = session_id.replace("/", "_")[:64]
    37|    
    38|    status = {
    39|        "event": event_name,
    40|        "session_id": session_id,
    41|        "timestamp": time.time(),
    42|        "iso_time": time.strftime("%Y-%m-%d %H:%M:%S"),
    43|        "data": data,
    44|    }
    45|    
    46|    if event_name == "Stop":
    47|        # Stop 事件写主文件（覆盖）
    48|        status_file = STATUS_DIR / f"{safe_id}.json"
    49|    else:
    50|        # 其他事件写带时间戳的独立文件（不覆盖）
    51|        ts = int(time.time() * 1000)
    52|        status_file = STATUS_DIR / f"{safe_id}-{event_name}-{ts}.json"
    53|    
    54|    tmp_file = status_file.with_suffix(".tmp")
    55|    with open(tmp_file, "w") as f:
    56|        json.dump(status, f, ensure_ascii=False, indent=2)
    57|    tmp_file.rename(status_file)
    58|
    59|def parse_transcript_stats(transcript_path: str) -> dict:
    60|    """从 transcript JSONL 解析统计信息"""
    61|    stats = {
    62|        "total_turns": 0,
    63|        "total_tool_calls": 0,
    64|        "total_tokens": 0,
    65|        "tools_used": [],
    66|        "files_modified": [],
    67|        "errors": [],
    68|    }
    69|    if not transcript_path or not os.path.exists(transcript_path):
    70|        return stats
    71|    
    72|    try:
    73|        with open(transcript_path, "r") as f:
    74|            for line in f:
    75|                try:
    76|                    obj = json.loads(line)
    77|                except json.JSONDecodeError:
    78|                    continue
    79|                
    80|                msg_type = obj.get("type", "")
    81|                
    82|                # 统计 assistant 消息
    83|                if msg_type == "assistant":
    84|                    stats["total_turns"] += 1
    85|                    content = obj.get("message", {}).get("content", [])
    86|                    if isinstance(content, list):
    87|                        for block in content:
    88|                            if isinstance(block, dict) and block.get("type") == "tool_use":
    89|                                stats["total_tool_calls"] += 1
    90|                                tool_name = block.get("name", "unknown")
    91|                                if tool_name not in stats["tools_used"]:
    92|                                    stats["tools_used"].append(tool_name)
    93|                
    94|                # 统计 token 使用
    95|                usage = obj.get("usage", {})
    96|                if usage:
    97|                    stats["total_tokens"] += usage.get("output_tokens", 0)
    98|                
    99|                # 提取修改的文件
   100|                if msg_type == "tool_result":
   101|                    tool_input = obj.get("input", {})
   102|                    if isinstance(tool_input, dict):
   103|                        file_path = tool_input.get("file_path") or tool_input.get("path", "")
   104|                        if file_path and file_path not in stats["files_modified"]:
   105|                            stats["files_modified"].append(file_path)
   106|    except Exception:
   107|        pass
   108|    
   109|    return stats
   110|
   111|def main():
   112|    event = read_stdin_event()
   113|    if not event:
   114|        return
   115|    
   116|    event_name = event.get("hook_event_name", "Unknown")
   117|    session_id = event.get("session_id", "")
   118|    cwd = event.get("cwd", "")
   119|    transcript_path = event.get("transcript_path", "")
   120|    stop_hook_reason = event.get("stop_hook_reason", "")
   121|    
   122|    # 构建事件数据
   123|    data = {
   124|        "cwd": cwd,
   125|        "stop_reason": stop_hook_reason,
   126|    }
   127|    
   128|    # Stop 事件：解析 transcript 获取统计
   129|    if event_name == "Stop" and transcript_path:
   130|        data["transcript_path"] = transcript_path
   131|        data["stats"] = parse_transcript_stats(transcript_path)
   132|        data["last_message"] = event.get("last_assistant_message", "")[:500]
   133|    
   134|    # Notification 事件
   135|    if event_name == "Notification":
   136|        data["message"] = event.get("message", "")
   137|    
   138|    # PostToolUse 事件：记录工具使用
   139|    if event_name == "PostToolUse":
   140|        data["tool_name"] = event.get("tool_name", "")
   141|        data["tool_input"] = str(event.get("tool_input", {}))[:200]
   142|    
   143|    # SubagentStop 事件
   144|    if event_name == "SubagentStop":
   145|        data["agent_id"] = event.get("agent_id", "")
   146|    
   147|    write_status(session_id, event_name, data)
   148|    
   149|    # PostToolUse 时更新统一进度文件
   150|    if event_name == "PostToolUse":
   151|        update_live_progress(session_id)
   152|
   153|
   154|def update_live_progress(session_id: str):
   155|    """每次 PostToolUse 后扫描所有事件，写统一进度文件"""
   156|    if not session_id:
   157|        return
   158|    safe_id = session_id.replace("/", "_")[:64]
   159|    
   160|    tool_counts = {}
   161|    total = 0
   162|    earliest = None
   163|    
   164|    for f in sorted(STATUS_DIR.glob(f"{safe_id}-PostToolUse-*.json")):
   165|        try:
   166|            with open(f) as fh:
   167|                s = json.load(fh)
   168|            ts = s.get("timestamp", 0)
   169|            if earliest is None or ts < earliest:
   170|                earliest = ts
   171|            tool = s.get("data", {}).get("tool_name", "?")
   172|            tool_counts[tool] = tool_counts.get(tool, 0) + 1
   173|            total += 1
   174|        except Exception:
   175|            continue
   176|    
   177|    elapsed = time.time() - earliest if earliest else 0
   178|    
   179|    progress = {
   180|        "session_id": session_id,
   181|        "elapsed": round(elapsed, 1),
   182|        "completed": False,
   183|        "total_tool_calls": total,
   184|        "tool_breakdown": tool_counts,
   185|        "last_tool": tool,
   186|        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
   187|    }
   188|    
   189|    progress_file = STATUS_DIR / f"{safe_id}-progress.json"
   190|    tmp = progress_file.with_suffix(".tmp")
   191|    with open(tmp, "w") as fh:
   192|        json.dump(progress, fh, ensure_ascii=False, indent=2)
   193|    tmp.rename(progress_file)
   194|
   195|if __name__ == "__main__":
   196|    main()
   197|