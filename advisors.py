"""Read-only advisor workers: the logic behind ask_worker / list_workers.

Workers live in workers.json (see workers.example.json), re-read on every call,
so moving a vLLM host or swapping the served model is an edit to one file — no
restart. A worker with an empty "model" asks its server which id it serves.

Read-only is enforced by structure, not by the CLI's own flag: qwen keeps
--approval-mode plan as a cheap extra, agy runs without --mode plan (too slow,
see _build), and neither is trusted. The worker runs in a
throwaway copy of the project, and the original is fingerprinted before/after so
an absolute-path write that escapes the copy is reported, not silent.
"""

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx

WORKERS_FILE = Path(os.environ.get("WORKERS_FILE") or Path(__file__).resolve().parent / "workers.json")
PROBE_TIMEOUT = 5.0
MAX_COPY_BYTES = 200 * 1024 * 1024
SKIP_DIRS = {".git", "node_modules", ".venv", "__pycache__"}

PERSONAS_DIR = Path(os.environ.get("PERSONAS_DIR") or Path(__file__).resolve().parent / "personas")


def roles() -> list[str]:
    """Roles = the *.md files in personas/ (minus _base). Drop a file in to add one."""
    return sorted(p.stem for p in PERSONAS_DIR.glob("*.md") if not p.stem.startswith("_"))


def persona(role: str) -> str:
    """Inlined into the prompt on every call: a small model acts on text it can see,
    not on a pointer to a file, and the CLIs' own global persona (QWEN.md/GEMINI.md)
    tells the worker to write files — _base.md overrides that explicitly."""
    return (PERSONAS_DIR / "_base.md").read_text() + "\n" + (PERSONAS_DIR / f"{role}.md").read_text() + "\n\n"


def load_workers(fallback_url: str = "") -> tuple[str, dict[str, dict[str, Any]]]:
    """-> (default_worker, {name: spec}). Missing file falls back to MODEL_BASE_URL."""
    if WORKERS_FILE.exists():
        data = json.loads(WORKERS_FILE.read_text())
        workers = data["workers"]
        return data.get("default") or next(iter(workers)), workers
    if fallback_url:
        return "local", {"local": {"kind": "qwen", "base_url": fallback_url, "model": ""}}
    return "", {}


def served_model(base_url: str) -> str | None:
    """First model id the server reports, or None if it cannot be reached."""
    try:
        with httpx.Client(timeout=PROBE_TIMEOUT) as c:
            ids = [m["id"] for m in c.get(f"{base_url}/models").json()["data"] if m.get("id")]
        return ids[0] if ids else None
    except Exception:
        return None


def list_workers(fallback_url: str = "") -> dict[str, Any]:
    default, workers = load_workers(fallback_url)
    out = {}
    for name, spec in workers.items():
        if spec["kind"] == "qwen":
            model = served_model(spec["base_url"])
            out[name] = {
                "kind": "qwen", "base_url": spec["base_url"],
                "configured_model": spec.get("model") or None, "served_model": model,
                "status": "online" if model else "offline",
            }
        else:
            out[name] = {"kind": spec["kind"], "status": "cli (not probed)"}
    return {"default": default, "config": str(WORKERS_FILE), "workers": out}


def _project_files(root: Path) -> list[Path] | None:
    """Files a worker should see: tracked + untracked-not-ignored. None = too big."""
    r = subprocess.run(["git", "-C", str(root), "ls-files", "-co", "--exclude-standard", "-z"],
                       capture_output=True, timeout=20)
    if r.returncode == 0:
        rel = [Path(p) for p in r.stdout.decode(errors="replace").split("\0") if p]
    else:
        rel = [p.relative_to(root) for p in root.rglob("*")
               if not set(p.relative_to(root).parts) & SKIP_DIRS]
    files = [p for p in rel if (root / p).is_file() and not (root / p).is_symlink()]
    if sum((root / p).stat().st_size for p in files) > MAX_COPY_BYTES:
        return None
    return files


def _fingerprint(root: Path, files: list[Path]) -> dict[str, tuple[int, int]]:
    out = {}
    for p in files:
        try:
            st = (root / p).stat()
            out[str(p)] = (st.st_size, st.st_mtime_ns)
        except OSError:
            out[str(p)] = (-1, -1)
    return out


def _changed(before: dict, after: dict) -> list[str]:
    return sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))


def _build(spec: dict, worker_cmd_prompt: str, model: str, timeout: int) -> tuple[list[str], dict[str, str]]:
    if spec["kind"] == "qwen":
        cmd = [spec.get("bin", "qwen"), "-p", worker_cmd_prompt, "-o", "json",
               "--approval-mode", "plan", "--auth-type", "openai"]
        env = {"OPENAI_API_KEY": "not-needed", "OPENAI_BASE_URL": spec["base_url"], "OPENAI_MODEL": model}
        return cmd, env
    if spec["kind"] == "agy":
        # No `--mode plan`: it guards nothing here (the copy does) and makes agy
        # write a plan + walkthrough artifact on every turn -- measured 2026-10-07,
        # a one-line file read took 47 s in plan mode vs 14 s without. Not the
        # whole story: a real diff review still exceeded 600 s afterwards.
        cmd = [spec.get("bin", "agy"), "-p", worker_cmd_prompt, "--output-format", "json",
               "--print-timeout", f"{timeout}s"]
        if spec.get("model"):  # else agy falls back to the IDE-shared settings.json model
            cmd += ["--model", spec["model"]]
        return cmd, {}
    raise ValueError(f"unknown worker kind: {spec['kind']}")


def _parse(kind: str, stdout: str) -> tuple[bool, str]:
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return False, stdout
    if kind == "agy":
        return data.get("status") == "SUCCESS", data.get("response", "")
    result = next((e for e in reversed(data) if isinstance(e, dict) and e.get("type") == "result"), {})
    return not result.get("is_error", True), result.get("result", "")


def ask_worker(prompt: str, working_dir: str, worker: str = "", role: str = "ask",
               timeout: int = 600, fallback_url: str = "") -> dict[str, Any]:
    default, workers = load_workers(fallback_url)
    name = worker or default
    if name not in workers:
        return {"status": "error", "error": f"unknown worker {name!r}; known: {sorted(workers)}"}
    if role not in roles():
        return {"status": "error", "error": f"unknown role {role!r}; known: {roles()}"}
    spec = workers[name]
    root = Path(working_dir).resolve()
    if not root.is_dir():
        return {"status": "error", "error": f"Working directory does not exist: {working_dir}"}

    model = ""
    if spec["kind"] == "qwen":
        model = spec.get("model") or served_model(spec["base_url"]) or ""
        if not model:
            return {"status": "error", "error": f"worker {name!r} offline: {spec['base_url']}"}

    files = _project_files(root)
    if files is None:
        return {"status": "error", "error": f"project larger than {MAX_COPY_BYTES // 2**20} MB; "
                                            "pass a narrower working_dir"}

    sandbox = Path(tempfile.mkdtemp(prefix="advisor-"))
    try:
        for p in files:
            (sandbox / p).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / p, sandbox / p)
        orig_before = _fingerprint(root, files)
        copy_before = _fingerprint(sandbox, files)

        cmd, env = _build(spec, persona(role) + prompt, model, timeout)
        # The worker CLI runs the user's own hooks. Left on, the vault Stop hooks
        # file every worker run as an "Oturum Özeti" under a throwaway
        # tmp--advisor-* mem-lite namespace (13 such rows by 2026-10-07).
        env["VAULT_HOOKS_OFF"] = "1"
        started = time.monotonic()
        try:
            r = subprocess.run(cmd, cwd=str(sandbox), capture_output=True, text=True,
                               timeout=timeout, stdin=subprocess.DEVNULL, env={**os.environ, **env})
        except subprocess.TimeoutExpired:
            return {"status": "timeout", "worker": name, "error": f"exceeded {timeout}s"}
        except FileNotFoundError:
            return {"status": "error", "worker": name, "error": f"{cmd[0]} CLI not found in PATH"}

        ok, text = _parse(spec["kind"], r.stdout)
        text = text.replace(str(sandbox), str(root))  # findings must point at the real tree
        after_files = [p.relative_to(sandbox) for p in sandbox.rglob("*") if p.is_file()]
        wrote_in_copy = _changed(copy_before, _fingerprint(sandbox, after_files))
        # Re-list, so a NEW file an absolute-path write dropped into the original shows up.
        orig_now = sorted(set(files) | set(_project_files(root) or []))
        touched_original = _changed(orig_before, _fingerprint(root, orig_now))

        result = {
            "status": "success" if ok and r.returncode == 0 else "error",
            "worker": name, "model": model or spec["kind"], "role": role,
            "elapsed_s": round(time.monotonic() - started, 1), "response": text,
            "write_attempts_discarded": wrote_in_copy,
            "original_modified": touched_original,
        }
        if touched_original:
            result["status"] = "VIOLATION"
            result["warning"] = "worker modified the ORIGINAL tree despite the sandbox; inspect with git status/diff"
        if not ok:
            result["stderr"] = r.stderr[-1500:]
        return result
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)
