#!/usr/bin/env python3
"""Check local-model-mcp's environment and connectivity to the model server."""

import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")
except ImportError:
    pass  # dependency-light by design; .env is optional, real env vars still work

# No real host baked in here — this file ships in a public repo. Keep this in
# sync with server.py's FALLBACK_MODEL (#411: the two drifted once already,
# check_env.py reporting "healthy" while server.py 404'd on a stale model id).
DEFAULT_BASE_URL = "http://localhost:8002/v1"
DEFAULT_MODEL = "/models/qwen3.8-27b"


def main() -> int:
    parser = argparse.ArgumentParser(description="Check local-model-mcp's environment")
    parser.add_argument("--timeout", type=int, default=5, help="Request timeout in seconds (default: 5)")
    parser.add_argument("--quiet", "-q", action="store_true", help="Suppress env var output")
    args = parser.parse_args()

    base_url = os.environ.get("QWEN_BASE_URL", DEFAULT_BASE_URL)
    model = os.environ.get("QWEN_MODEL", DEFAULT_MODEL)

    if not args.quiet:
        if os.environ.get("QWEN_BASE_URL"):
            print(f"✓ QWEN_BASE_URL={os.environ['QWEN_BASE_URL']}")
        else:
            print(f"  QWEN_BASE_URL=(default) {DEFAULT_BASE_URL} — set it in .env or export it")

        if os.environ.get("QWEN_MODEL"):
            print(f"✓ QWEN_MODEL={model}")
        else:
            print(f"  QWEN_MODEL=(default) {model}")

    if not base_url.startswith(("http://", "https://")):
        print(f"✗ Invalid URL — must start with http:// or https://: {base_url}", file=sys.stderr)
        return 1

    # Check server connectivity
    models_url = f"{base_url}/models"
    try:
        req = urllib.request.Request(models_url, method="GET")
        req.add_header("Accept", "application/json")
        start = time.monotonic()
        with urllib.request.urlopen(req, timeout=args.timeout) as resp:
            latency_ms = int((time.monotonic() - start) * 1000)
            if resp.status == 200:
                data = json.loads(resp.read().decode())
                model_ids = [m["id"] for m in data.get("data", [])]
                print(f"✓ Server reachable — models: {', '.join(model_ids)} ({latency_ms}ms)")
                return 0
            else:
                print(f"✗ Server responded with HTTP {resp.status}")
                return 1
    except urllib.error.HTTPError as e:
        print(f"✗ Server unreachable: HTTP {e.code} {e.reason}")
        return 1
    except urllib.error.URLError as e:
        print(f"✗ Server unreachable: {e.reason}")
        return 1
    except Exception as e:
        print(f"✗ Server unreachable: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
