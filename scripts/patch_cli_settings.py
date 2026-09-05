#!/usr/bin/env python3
"""Add the permission entries the Qwen Code CLI needs to run unattended (yolo
mode) to the user's global ~/.qwen/settings.json. --approval-mode yolo
bypasses per-call prompts but not settings.json hard restrictions -- this is
what actually grants the access.

Only runs when WORKER_CLI_BIN is unset or "qwen" (the default) -- this is the
one place left that's genuinely specific to that CLI's own settings schema.
If you've pointed WORKER_CLI_BIN at a different coding-agent CLI, its
permission model is its own and this script has nothing to patch.
"""
import json
import os

worker_cli = os.environ.get("WORKER_CLI_BIN", "qwen")
if worker_cli != "qwen":
    print(f"   WORKER_CLI_BIN={worker_cli} -- skipping Qwen-specific settings.json patch")
    raise SystemExit(0)

settings_path = os.path.expanduser("~/.qwen/settings.json")
with open(settings_path) as f:
    data = json.load(f)

required_allow = [
    "Write(**)", "Edit(**)", "Read(**)",
    "Bash(git *)", "Bash(python3 *)", "Bash(pip *)", "Bash(uv *)",
    "Bash(npm *)", "Bash(node *)", "Bash(npx *)",
    "Bash(go *)", "Bash(cargo *)", "Bash(make *)",
    "Bash(ls *)", "Bash(find *)", "Bash(grep *)", "Bash(cat *)",
    "Bash(sed *)", "Bash(awk *)", "Bash(mkdir *)", "Bash(mv *)",
    "Bash(cp *)", "Bash(rm *)", "Bash(chmod *)",
    "Bash(ansible *)", "Bash(ansible-vault *)", "Bash(ansible-playbook *)",
    "Bash(kubectl *)", "Bash(docker *)", "Bash(gh *)",
    "Bash(curl *)", "Bash(which *)", "Bash(echo *)",
    "Bash(wc *)", "Bash(head *)", "Bash(tail *)",
    "Bash(sort *)", "Bash(uniq *)",
]

perms = data.setdefault("permissions", {})
allow = perms.setdefault("allow", [])

added = []
for entry in required_allow:
    if entry not in allow:
        allow.append(entry)
        added.append(entry)

with open(settings_path, "w") as f:
    json.dump(data, f, indent=2)

if added:
    print(f"   Added {len(added)} permission entries")
else:
    print("   Permissions already up to date")
