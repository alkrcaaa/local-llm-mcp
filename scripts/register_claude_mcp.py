#!/usr/bin/env python3
"""Register this server as the 'qwen' MCP entry in ~/.claude/claude.json.

Usage: register_claude_mcp.py <python-binary> <server.py-path>
"""
import json
import os
import sys

python_bin, server_py = sys.argv[1], sys.argv[2]

claude_json = os.path.expanduser("~/.claude/claude.json")
with open(claude_json) as f:
    data = json.load(f)

data.setdefault("mcpServers", {})["qwen"] = {
    "command": python_bin,
    "args": [server_py],
    "env": {},
}

with open(claude_json, "w") as f:
    json.dump(data, f, indent=2)

print(f"   Registered: qwen -> {python_bin} {server_py}")
