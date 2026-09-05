#!/bin/bash
# install.sh — local-model-mcp setup
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
QWEN_SETTINGS="$HOME/.qwen/settings.json"

echo "Installing local-model-mcp..."
echo "  Location: $SCRIPT_DIR"

if [ ! -f "$SCRIPT_DIR/.env" ]; then
    cp "$SCRIPT_DIR/.env.example" "$SCRIPT_DIR/.env"
    echo "  Created .env from .env.example -- edit it to point at your model server"
else
    echo "  .env already exists"
fi

if [ ! -d "$VENV_DIR" ]; then
    echo "  Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
else
    echo "  Virtual environment already exists"
fi

echo "  Installing dependencies..."
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet -r "$SCRIPT_DIR/requirements.txt"

# Ensure the Qwen Code CLI can write/edit files in any project, regardless of
# project-level settings.json. --approval-mode yolo bypasses prompts but NOT
# settings.json hard restrictions -- global permissions are the authoritative fix.
if command -v python3 &>/dev/null && [ -f "$QWEN_SETTINGS" ]; then
    echo "  Patching global ~/.qwen/settings.json with write permissions..."
    python3 "$SCRIPT_DIR/scripts/patch_cli_settings.py"
elif ! command -v python3 &>/dev/null; then
    echo "  python3 not found -- skipping ~/.qwen/settings.json patch (do it manually)"
elif [ ! -f "$QWEN_SETTINGS" ]; then
    echo "  ~/.qwen/settings.json not found -- if WORKER_CLI_BIN is the default (qwen), install the Qwen Code CLI first; otherwise this step doesn't apply to your CLI."
fi

CLAUDE_JSON="$HOME/.claude/claude.json"
if [ -f "$CLAUDE_JSON" ] && command -v python3 &>/dev/null; then
    echo "  Registering local-model-mcp in ~/.claude/claude.json..."
    python3 "$SCRIPT_DIR/scripts/register_claude_mcp.py" "$VENV_DIR/bin/python" "$SCRIPT_DIR/server.py"
else
    echo "  ~/.claude/claude.json not found -- add the MCP server manually:"
    echo "  {\"local-model\": {\"command\": \"$VENV_DIR/bin/python\", \"args\": [\"$SCRIPT_DIR/server.py\"]}}"
fi

echo ""
echo "Done. Edit $SCRIPT_DIR/.env to point at your model server, then verify:"
echo "  .venv/bin/python check_env.py"
echo ""
