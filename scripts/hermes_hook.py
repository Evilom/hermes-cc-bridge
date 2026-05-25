#!/usr/bin/env python3
"""
Hermes ↔ Claude Code Hook Bridge

从 Claude Code 的 hook 事件中提取信息，写入状态文件供 cc_sdk.py 读取。
支持事件：Stop, Notification, PostToolUse, SubagentStop, SessionStart

状态文件：/tmp/hermes-cc-{session_id}.json
格式：{"event": "Stop", "session_id": "...", "timestamp": ..., "data": {...}}
"""

import json
import os
import sys
import time
from pathlib import Path

STATUS_DIR = Path("/tmp/hermes-cc-status")
STATUS_DIR.mkdir(parents=True, exist_ok=True)

def read_stdin_event() -> dict:
    """从 stdin 读取 CC 传来的 hook 事件数据"""
    try:
        if sys.stdin.isatty():
            return {}
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, IOError):
        return {}

def write_status(session_id: str, event_name: str, data: dict):
    """写入状态文件。Stop 事件覆盖主文件，其他事件写独立文件。"""
    if not session_id:
        session_id = data.get("session_id", "unknown")
    
    safe_id = session_id.replace("/", "_")[:64]
    
    status = {
        "event": event_name,
        "session_id": session_id,
        "timestamp": time.time(),
        "iso_time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "data": data,
    }
    
    if event_name == "Stop":
        # Stop 事件写主文件（覆盖）
        status_file = STATUS_DIR / f"{safe_id}.json"
    else:
        # 其他事件写带时间戳的独立文件（不覆盖）
        ts = int(time.time() * 1000)
        status_file = STATUS_DIR / f"{safe_id}-{event_name}-{ts}.json"
    
    tmp_file = status_file.with_suffix(".tmp")
    with open(tmp_file, "w") as f:
        json.dump(status, f, ensure_ascii=False, indent=2)
    tmp_file.rename(status_file)

def parse_transcript_stats(transcript_path: str) -> dict:
    """从 transcript JSONL 解析统计信息"""
    stats = {
        "total_turns": 0,
        "total_tool_calls": 0,
        "total_tokens": 0,
        "tools_used": [],
        "files_modified": [],
        "errors": [],
    }
    if not transcript_path or not os.path.exists(transcript_path):
        return stats
    
    try:
        with open(transcript_path, "r") as f:
            for line in f:
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                
                msg_type = obj.get("type", "")
                
                # 统计 assistant 消息
                if msg_type == "assistant":
                    stats["total_turns"] += 1
                    content = obj.get("message", {}).get("content", [])
                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "tool_use":
                                stats["total_tool_calls"] += 1
                                tool_name = block.get("name", "unknown")
                                if tool_name not in stats["tools_used"]:
                                    stats["tools_used"].append(tool_name)
                
                # 统计 token 使用
                usage = obj.get("usage", {})
                if usage:
                    stats["total_tokens"] += usage.get("output_tokens", 0)
                
                # 提取修改的文件
                if msg_type == "tool_result":
                    tool_input = obj.get("input", {})
                    if isinstance(tool_input, dict):
                        file_path = tool_input.get("file_path") or tool_input.get("path", "")
                        if file_path and file_path not in stats["files_modified"]:
                            stats["files_modified"].append(file_path)
    except Exception:
        pass
    
    return stats

def main():
    event = read_stdin_event()
    if not event:
        return
    
    event_name = event.get("hook_event_name", "Unknown")
    session_id = event.get("session_id", "")
    cwd = event.get("cwd", "")
    transcript_path = event.get("transcript_path", "")
    stop_hook_reason = event.get("stop_hook_reason", "")
    
    # 构建事件数据
    data = {
        "cwd": cwd,
        "stop_reason": stop_hook_reason,
    }
    
    # Stop 事件：解析 transcript 获取统计
    if event_name == "Stop" and transcript_path:
        data["transcript_path"] = transcript_path
        data["stats"] = parse_transcript_stats(transcript_path)
        data["last_message"] = event.get("last_assistant_message", "")[:500]
    
    # Notification 事件
    if event_name == "Notification":
        data["message"] = event.get("message", "")
    
    # PostToolUse 事件：记录工具使用
    if event_name == "PostToolUse":
        data["tool_name"] = event.get("tool_name", "")
        data["tool_input"] = str(event.get("tool_input", {}))[:200]
    
    # SubagentStop 事件
    if event_name == "SubagentStop":
        data["agent_id"] = event.get("agent_id", "")
    
    write_status(session_id, event_name, data)
    
    # PostToolUse 时更新统一进度文件
    if event_name == "PostToolUse":
        update_live_progress(session_id)


def update_live_progress(session_id: str):
    """每次 PostToolUse 后扫描所有事件，写统一进度文件"""
    if not session_id:
        return
    safe_id = session_id.replace("/", "_")[:64]
    
    tool_counts = {}
    total = 0
    earliest = None
    
    for f in sorted(STATUS_DIR.glob(f"{safe_id}-PostToolUse-*.json")):
        try:
            with open(f) as fh:
                s = json.load(fh)
            ts = s.get("timestamp", 0)
            if earliest is None or ts < earliest:
                earliest = ts
            tool = s.get("data", {}).get("tool_name", "?")
            tool_counts[tool] = tool_counts.get(tool, 0) + 1
            total += 1
        except Exception:
            continue
    
    elapsed = time.time() - earliest if earliest else 0
    
    progress = {
        "session_id": session_id,
        "elapsed": round(elapsed, 1),
        "completed": False,
        "total_tool_calls": total,
        "tool_breakdown": tool_counts,
        "last_tool": tool,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    
    progress_file = STATUS_DIR / f"{safe_id}-progress.json"
    tmp = progress_file.with_suffix(".tmp")
    with open(tmp, "w") as fh:
        json.dump(progress, fh, ensure_ascii=False, indent=2)
    tmp.rename(progress_file)

if __name__ == "__main__":
    main()
