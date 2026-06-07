#!/usr/bin/env python3
"""Invoke shani_reset_workflow (MCP tool) — HTTP /workflows/1/reset does not exist."""
import asyncio
import json
import sys

sys.path.insert(0, "/mnt/d/brahm")
sys.path.insert(0, "/mnt/d/brahm/agents/shani")


async def main():
    from brahm.agents.shani import shani_reset_workflow
    result = await shani_reset_workflow({"workflow_id": 1, "from_stage": "S5"})
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
