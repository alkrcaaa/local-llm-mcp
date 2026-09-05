#!/usr/bin/env python3
"""MCP server bridging Claude Code to a local model via the Qwen Code CLI.

Exposes three tools:
- qwen_execute: Run task on Qwen
- qwen_health_check: Verify Qwen availability
- qwen_execute_with_context: Run task with file context
"""

import os
import subprocess
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

# Explicit path, not cwd-relative: an MCP client launches this server with the
# caller's project directory as cwd (whatever the user has open), not this
# script's own directory — a bare load_dotenv() would silently find nothing
# and the "convert to .env" config would work for exactly one test run.
load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")

# QWEN_BASE_URL / QWEN_MODEL: set in .env (see .env.example), or exported in
# the shell, or via the MCP client's own "env" block in its server config —
# any of the three works; .env is just the lowest-effort default so a fresh
# clone works after one `cp .env.example .env` + edit.
QWEN_BASE_URL = os.environ.get("QWEN_BASE_URL", "")
QWEN_MODEL = os.environ.get("QWEN_MODEL", "")
# Only reached when the host cannot be asked; see _model_id().
FALLBACK_MODEL = "/models/qwen3.8-27b"
HEALTH_CHECK_TIMEOUT = 5.0
EXECUTION_TIMEOUT = 300.0

_resolved_model = ""


def _model_id() -> str:
    """The model to ask for: the configured one, else whatever the host serves.

    A hard-coded default is a time bomb aimed at a vLLM box that gets relaunched
    on a newer checkpoint. The served id moved from qwen3.6-27b to qwen3.8-27b
    and every qwen_execute began failing with a 404 naming the model -- while
    check_env.py, carrying its own copy of the default, had already been updated
    and reported the setup healthy. Ask the server which id it answers to, and
    fall back to a literal only when it cannot be reached.
    """
    global _resolved_model
    if QWEN_MODEL:
        return QWEN_MODEL
    if _resolved_model:
        return _resolved_model
    try:
        with httpx.Client(timeout=HEALTH_CHECK_TIMEOUT) as client:
            served = client.get(f"{QWEN_BASE_URL}/models").json()["data"]
        ids = [m["id"] for m in served if isinstance(m, dict) and m.get("id")]
        if ids:
            _resolved_model = ids[0]
            return _resolved_model
    except Exception:
        pass
    return FALLBACK_MODEL

# Sandwich approach: prefix + task + suffix to combat "lost in the middle" on long prompts
QWEN_WORKER_PREFIX = (
    "[INSTRUCTION: You are a code-writing worker. "
    "Write complete, runnable code only. "
    "No descriptions or explanations instead of code. "
    "No markdown fences around output. "
    "Write the full file content, not partial snippets. "
    "Before writing, read any file you will modify. "
    "After writing a file, read it back to confirm it is complete and syntactically valid.]\n\n"
)
QWEN_WORKER_SUFFIX = (
    "\n\n[REMINDER: The deliverable is files written to disk, not text. "
    "Output only complete, runnable code. No descriptions. No markdown fences. "
    "Full file content. Verify every written file by reading it back; "
    "if a test or run command was specified in the task, run it and report the result.]"
)

# All tools Qwen Code exposes — passed via --allowed-tools to bypass
# project-level settings.json restrictions on any machine/project
QWEN_ALLOWED_TOOLS = [
    "write_file",
    "edit",
    "read_file",
    "run_shell_command",
    "glob",
    "grep_search",
    "list_directory",
    "monitor",
]

server = FastMCP("local-model-mcp", "1.0.0")


@server.tool()
def qwen_health_check() -> dict[str, Any]:
    """Check if Qwen vLLM server is online and list available models."""
    if not QWEN_BASE_URL:
        return {"status": "offline", "error": "QWEN_BASE_URL is not set. Copy .env.example to .env and set it."}
    try:
        with httpx.Client(timeout=HEALTH_CHECK_TIMEOUT) as client:
            response = client.get(f"{QWEN_BASE_URL}/models")
            response.raise_for_status()
            data = response.json()
            models = [m.get("id", str(m)) for m in data.get("data", [])]
            return {"status": "online", "models": models, "url": QWEN_BASE_URL}
    except httpx.ConnectError:
        return {"status": "offline", "error": f"Cannot connect to {QWEN_BASE_URL}"}
    except httpx.TimeoutException:
        return {"status": "offline", "error": f"Timeout connecting to {QWEN_BASE_URL}"}
    except Exception as e:
        return {"status": "offline", "error": str(e)}


def _load_project_conventions(working_path: Path) -> str:
    """Load project conventions from qwen-memory/project-conventions.md if present."""
    conventions_path = working_path / "qwen-memory" / "project-conventions.md"
    if not conventions_path.exists():
        return ""
    try:
        content = conventions_path.read_text(errors="replace").strip()
        return f"\n\nPROJECT CONVENTIONS:\n{content}\n"
    except Exception:
        return ""


@server.tool()
def qwen_execute(task: str, working_dir: str, files: list[str] | None = None) -> dict[str, Any]:
    """Execute a coding task on the local Qwen model.

    Args:
        task: Detailed task description. Be explicit: include file paths, what to
              change, and what success looks like. Qwen has no memory of prior calls.
        working_dir: Absolute path to the project root where Qwen will run.
                     Must match the project containing the files to edit.
        files: Optional list of file paths to mention in task context (informational).

    Returns:
        dict with 'status', 'stdout', 'stderr', 'return_code', 'files_changed'
    """
    if not QWEN_BASE_URL:
        return {"status": "error", "error": "QWEN_BASE_URL is not set. Copy .env.example to .env and set it."}
    working_path = Path(working_dir).resolve()
    if not working_path.exists():
        return {"status": "error", "error": f"Working directory does not exist: {working_dir}"}

    env = {
        "OPENAI_API_KEY": "not-needed",
        "OPENAI_BASE_URL": QWEN_BASE_URL,
        "OPENAI_MODEL": _model_id(),
    }

    # Snapshot changed files before execution (git-based, reliable)
    files_before = _git_changed_files(working_path)

    try:
        cmd = [
            "qwen", "-p", QWEN_WORKER_PREFIX + _load_project_conventions(working_path) + task + QWEN_WORKER_SUFFIX,
            "--auth-type", "openai",
            "--approval-mode", "yolo",
            "--output-format", "text",
            "--allowed-tools", *QWEN_ALLOWED_TOOLS,
        ]
        result = subprocess.run(
            cmd,
            cwd=str(working_path),
            capture_output=True,
            text=True,
            timeout=EXECUTION_TIMEOUT,
            stdin=subprocess.DEVNULL,
            env={**os.environ, **env},
        )

        files_after = _git_changed_files(working_path)
        files_changed = _diff_file_lists(files_before, files_after)

        # Warn only when:
        # - git is available (not None)
        # - repo was clean before Qwen ran (files_before is empty)
        # - nothing new appeared after
        # If files_before was non-empty, Qwen may have edited already-dirty files
        # which git snapshot can't detect (after - before = {} in that case).
        is_git = files_before is not None
        repo_was_clean = is_git and len(files_before) == 0
        did_nothing = (
            result.returncode == 0
            and repo_was_clean
            and not files_changed
            and result.stdout.strip()
        )

        return {
            "status": "success" if result.returncode == 0 else "error",
            "return_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "files_changed": files_changed,
            "warning": (
                "Qwen responded but made no file changes. "
                "Task description may be too vague, or Qwen explained instead of acting. "
                "Try rephrasing with explicit file paths and concrete instructions."
            ) if did_nothing else None,
        }
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "error": f"Task exceeded {EXECUTION_TIMEOUT}s timeout"}
    except FileNotFoundError:
        return {"status": "error", "error": "qwen CLI not found. Ensure Qwen is installed and in PATH."}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@server.tool()
def qwen_execute_with_context(
    task: str,
    working_dir: str,
    context_files: list[str],
) -> dict[str, Any]:
    """Execute task with file contents prepended to the prompt.

    Use this when Qwen needs to read existing code before editing.
    Reads each file and injects content into the task prompt.

    Args:
        task: Task description
        working_dir: Absolute path to project root
        context_files: Absolute paths of files to read and inject as context

    Returns:
        Same as qwen_execute
    """
    working_path = Path(working_dir).resolve()
    context_parts = ["CONTEXT FILES:"]

    for file_path_str in context_files:
        file_path = Path(file_path_str).resolve()
        if not file_path.exists():
            context_parts.append(f"\n[File not found: {file_path}]")
            continue
        try:
            content = file_path.read_text(errors="replace")
            rel = file_path.relative_to(working_path) if file_path.is_relative_to(working_path) else file_path
            context_parts.append(f"\n### {rel}\n```\n{content}\n```")
        except Exception as e:
            context_parts.append(f"\n[Error reading {file_path}: {e}]")

    full_task = "\n".join(context_parts) + f"\n\nTASK:\n{task}"
    return qwen_execute(full_task, working_dir)


def _is_git_repo(working_path: Path) -> bool:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=str(working_path), capture_output=True, timeout=5
        )
        return r.returncode == 0
    except Exception:
        return False


def _git_changed_files(working_path: Path) -> set[str] | None:
    """Return set of dirty files (modified + staged + untracked), or None if not a git repo.

    Uses git status --porcelain which handles repos with no commits yet.
    """
    if not _is_git_repo(working_path):
        return None
    try:
        # --porcelain works on repos with no commits (unlike `git diff HEAD`)
        r = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(working_path), capture_output=True, text=True, timeout=10
        )
        if r.returncode != 0:
            return None
        files: set[str] = set()
        for line in r.stdout.splitlines():
            # porcelain format: "XY filename" or "XY old -> new" for renames
            parts = line[3:].split(" -> ")
            files.add(parts[-1].strip())
        return files
    except Exception:
        return None


def _diff_file_lists(before: set[str] | None, after: set[str] | None) -> list[str]:
    """Return files that appeared or changed between snapshots.

    Returns empty list (not a warning signal) when git is unavailable.
    """
    if before is None or after is None:
        return []  # non-git project — can't track changes
    return sorted(after - before)


if __name__ == "__main__":
    server.run()
