# local-model-mcp

MCP (Model Context Protocol) server that lets Claude Code (or any MCP client)
delegate coding tasks to a local model through a coding-agent CLI — pointed at
whatever OpenAI-compatible endpoint you run: a local vLLM instance, Ollama, or
any other self-hosted server. Ships configured for the
[Qwen Code CLI](https://github.com/QwenLM/qwen-code) by default (`WORKER_CLI_BIN=qwen`),
but the model behind `MODEL_BASE_URL` — and the CLI binary itself — are both
configurable.

## Architecture

```
┌─────────────────────────┐
│   Claude Code / Claude  │
│    (MCP Client)         │
└────────────┬────────────┘
             │ MCP Protocol
             ▼
┌─────────────────────────┐
│  local-model-mcp        │
│  (this project)         │
│                         │
│  - execute_task         │
│  - health_check         │
│  - execute_task_with_   │
│    context              │
└────────────┬────────────┘
             │ subprocess + env vars
             ▼
┌─────────────────────────┐
│   Worker CLI             │
│   (WORKER_CLI_BIN,       │
│    default: qwen)        │
│                         │
│ http://YOUR_SERVER:     │
│   8002/v1               │
└─────────────────────────┘
```

## Installation

### Prerequisites
- Python 3.9+
- The worker CLI in `PATH` (Qwen Code CLI by default)
- An OpenAI-compatible model server reachable at some `http://HOST:PORT/v1`

### Install

```bash
./install.sh
```

This will:
1. Copy `.env.example` to `.env` (edit it to point at your server)
2. Create a Python virtual environment and install dependencies
3. Register the server as `local-model` in `~/.claude/claude.json` if present
4. If `WORKER_CLI_BIN` is the default (`qwen`), patch `~/.qwen/settings.json` with the write/exec permissions the CLI needs to run unattended

### Configure

Edit `.env`:

```bash
MODEL_BASE_URL=http://your-server:8002/v1
MODEL_NAME=/models/your-model     # optional — auto-detected from the server if unset
WORKER_CLI_BIN=qwen               # optional — set to a different coding-agent CLI if not using Qwen Code
```

Verify:

```bash
.venv/bin/python check_env.py
```

### Register manually (if `install.sh` didn't find `claude.json`)

Add to `.mcp.json` or `~/.claude/claude.json`:

```json
{
  "mcpServers": {
    "local-model": {
      "command": "local-model-mcp/.venv/bin/python",
      "args": ["local-model-mcp/server.py"]
    }
  }
}
```

`.env` in this directory supplies `MODEL_BASE_URL`/`MODEL_NAME`/`WORKER_CLI_BIN` if you don't set them in the client's own `env` block — either works, the client's `env` block wins.

## Tools

### health_check()

Check if the model server is online.

**Returns:**
```json
{
  "status": "online" | "offline",
  "models": ["model1", "model2"],
  "url": "http://YOUR_SERVER:8002/v1",
  "error": "optional error message"
}
```

**Usage:**
```python
result = await client.call_tool("health_check", {})
if result["status"] == "online":
    # Safe to proceed with execute_task
```

---

### execute_task(task, working_dir, files?)

Execute a task on the model.

**Args:**
- `task` (str): Detailed task description
- `working_dir` (str): Absolute path to working directory (must exist)
- `files` (list[str], optional): File paths to mention in context

**Returns:**
```json
{
  "status": "success" | "error" | "timeout",
  "return_code": 0,
  "stdout": "task output",
  "stderr": "error output if any",
  "files_changed": ["src/file.py", "tests/test.py"],
  "warning": "null or warning string if the model responded but made no file changes"
}
```

**Return fields:**
- `files_changed` — Git-based diff of files modified or created (snapshots dirty state before and after execution). Requires a git repo; returns empty list for non-git projects.
- `warning` — Non-null when the run succeeded but `files_changed` is empty on a previously clean repo. Usually the task description was too vague. Rephrase with explicit file paths and concrete instructions.

**Example:**
```python
result = await client.call_tool("execute_task", {
    "task": "Write unit tests for add() function in src/utils.py",
    "working_dir": "/Users/you/workspace/myproject",
    "files": ["src/utils.py"]
})
```

---

### execute_task_with_context(task, working_dir, context_files)

Execute task with file contents prepended to the prompt.

**Args:**
- `task` (str): Task description
- `working_dir` (str): Absolute path to project root
- `context_files` (list[str]): File paths to read and include as context

**Returns:**
Same as `execute_task`

**Example:**
```python
result = await client.call_tool("execute_task_with_context", {
    "task": "Add error handling to the request function",
    "working_dir": "/Users/you/workspace/api",
    "context_files": [
        "/Users/you/workspace/api/src/client.py",
        "/Users/you/workspace/api/src/errors.py"
    ]
})
```

---

## Usage from Claude Code

In your prompt when invoking tools:

```markdown
AVAILABLE MCP:
- local-model (health_check, execute_task, execute_task_with_context)

WORKFLOW:
1. Call health_check() → verify the model server is online
2. IF online → call execute_task() with full task details
3. IF offline → implement yourself, notify user
```

### Best Practices

1. **Always health check first:**
   ```
   status = health_check()
   if status["status"] != "online":
       # Fall back to manual implementation
   ```

2. **Use absolute paths:**
   - `working_dir`: Must be absolute, must exist
   - `context_files`: Should be absolute for clarity

3. **Include full context in task string:**
   - The model has no memory of previous calls
   - Explicitly list file names, requirements, constraints

4. **Parse output carefully:**
   - `files_changed` uses git snapshots (before vs after)
   - Read full `stdout` for detailed results

5. **For multi-file tasks:**
   - Use `execute_task_with_context` to pass existing code
   - Or include file paths in task string with full instructions

## Prompt Engineering

### Sandwich Instruction Pattern

Every `execute_task` call automatically wraps the task with a PREFIX and SUFFIX to combat "description instead of code" failures. The model is instructed to write complete, runnable code — not explanations, partial snippets, or markdown-wrapped output.

The prompt structure is:

```
PREFIX + [project conventions] + task + SUFFIX
```

**PREFIX:**
```
[INSTRUCTION: You are a code-writing worker.
Write complete, runnable code only.
No descriptions or explanations instead of code.
No markdown fences around output.
Write the full file content, not partial snippets.]
```

**SUFFIX:**
```
[REMINDER: Output only complete, runnable code.
No descriptions. No markdown fences. Full file content.]
```

This "sandwich" approach combats the "lost in the middle" problem on long prompts — the model sees code-writing instructions both before and after the task.

### Automatic Project Conventions Injection

If the working directory contains `qwen-memory/project-conventions.md`, its contents are automatically injected between the PREFIX and the task. This directory name is kept as-is (not generalized along with the rest of this project) so existing per-project convention files keep working without a silent migration. This lets projects define conventions once and have them applied to every call without repeating them each time.

```bash
mkdir -p /path/to/project/qwen-memory
cat > /path/to/project/qwen-memory/project-conventions.md << 'EOF'
- Use Python type hints on all function signatures
- Write docstrings using Google style
- Run black and ruff before committing
- Tests live in tests/ and use pytest
EOF
```

The final prompt structure when conventions exist:

```
PREFIX
PROJECT CONVENTIONS:
(contents of qwen-memory/project-conventions.md)

task

SUFFIX
```

## Troubleshooting

### "<worker CLI> CLI not found"

```bash
which qwen   # or whatever WORKER_CLI_BIN points to
# If not in PATH, add to ~/.zshrc or ~/.bashrc:
export PATH="/path/to/qwen/bin:$PATH"
```

### "Cannot connect to http://YOUR_SERVER:8002/v1"

```bash
curl http://YOUR_SERVER:8002/v1/models
# If not running, start your model server first.
```

### Task timeout (>300s)

- Reduce task scope
- Break into smaller tasks
- Increase `EXECUTION_TIMEOUT` in `server.py` if needed

### Files not detected in output

- `files_changed` uses git snapshots (before vs after the run)
- If the repo was already dirty beforehand, pre-existing changes are not counted
- Non-git projects return an empty list
- Always read full `stdout` for file paths

## Development

```bash
.venv/bin/python -m pytest tests/        # run tests
.venv/bin/python server.py               # start server manually
DEBUG=true .venv/bin/python server.py    # enable MCP debug logging
```

## Environment Variables

Set via `.env` (see `.env.example`) or exported directly:

```bash
MODEL_BASE_URL=http://custom-host:8002/v1
MODEL_NAME=/models/custom-model
WORKER_CLI_BIN=qwen
```
