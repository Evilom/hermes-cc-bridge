#!/usr/bin/env python3
"""Basic usage: run a task and get structured result."""

import asyncio
import json
import sys
import os

# Add parent dir to path so we can import cc_sdk
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from cc_sdk import run_task


async def main():
    result = await run_task(
        prompt="List all Python files in the current directory and count their lines",
        cwd=".",
        max_turns=3,
        timeout=60,
    )

    print(f"Success: {result['success']}")
    print(f"Elapsed: {result['elapsed_seconds']}s")
    print(f"Tools used: {result['tool_count']}")
    print(f"Output: {result['text'][:200]}...")

    if result.get("progress"):
        print(f"Tool breakdown: {result['progress']['tool_breakdown']}")

    print(f"\nSession ID: {result['session_id']}")
    print(f"Resume with: cc_sdk.py \"continue\" --resume {result['session_id']}")


if __name__ == "__main__":
    asyncio.run(main())
