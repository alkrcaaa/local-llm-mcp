#!/usr/bin/env python3
"""Add the permission entries Qwen Code CLI needs to run unattended (yolo mode)
to the user's global ~/.qwen/settings.json. --approval-mode yolo bypasses
per-call prompts but not settings.json hard restrictions -- this is what
actually grants the access.
"""
import json
import os

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
