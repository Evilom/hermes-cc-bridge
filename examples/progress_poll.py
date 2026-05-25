#!/usr/bin/env python3
"""Poll progress of a running CC task."""

import json
import subprocess
import sys
import time


def poll_progress(session_id: str, interval: float = 2.0):
    """Poll progress until completion."""
    print(f"Polling progress for session: {session_id}")

    while True:
        result = subprocess.run(
            [sys.executable, "-c", f"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from cc_sdk import build_progress_report
import json
report = build_progress_report("{session_id}")
print(json.dumps(report))
"""],
            capture_output=True, text=True,
        )

        if result.returncode != 0:
            print(f"Error: {result.stderr}")
            break

        try:
            progress = json.loads(result.stdout.strip())
        except json.JSONDecodeError:
            print("Failed to parse progress")
            break

        elapsed = progress.get("elapsed", 0)
        tools = progress.get("total_tool_calls", 0)
        breakdown = progress.get("tool_breakdown", {})
        completed = progress.get("completed", False)
        last = progress.get("last_tool", {}).get("tool", "?") if progress.get("last_tool") else "?"

        print(f"  [{elapsed:.0f}s] tools={tools} last={last} breakdown={breakdown}")

        if completed:
            print("\n✅ Task completed!")
            stats = progress.get("stop_stats", {})
            if stats:
                print(f"  Turns: {stats.get('total_turns', '?')}")
                print(f"  Tools used: {stats.get('tools_used', [])}")
            break

        time.sleep(interval)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <session_id> [poll_interval_seconds]")
        sys.exit(1)

    sid = sys.argv[1]
    interval = float(sys.argv[2]) if len(sys.argv) > 2 else 2.0
    poll_progress(sid, interval)
