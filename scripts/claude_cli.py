"""Shared helper: run a headless Claude Code call (`claude -p`) and return structured JSON.

Uses the user's Claude subscription via the `claude` CLI; no API key needed.
"""
import json
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NO_TOOLS = ["Bash", "Edit", "Write", "Read", "WebFetch", "WebSearch"]


class ClaudeError(RuntimeError):
    pass


def ask(prompt, schema, read_files=False, timeout=1200, model=None):
    """Return (structured_output, meta). read_files=True allows the Read tool (for images)."""
    cmd = ["claude", "-p", prompt, "--output-format", "json", "--json-schema", json.dumps(schema),
           "--no-session-persistence"]
    if read_files:
        cmd += ["--allowedTools", "Read", "--disallowedTools", *[t for t in NO_TOOLS if t != "Read"]]
    else:
        cmd += ["--disallowedTools", *NO_TOOLS]
    if model:
        cmd += ["--model", model]
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    try:
        raw = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise ClaudeError(f"claude failed ({proc.returncode}): {proc.stderr[-1500:]} {proc.stdout[-1500:]}")
    if raw.get("is_error") or "structured_output" not in raw:
        raise ClaudeError(f"claude returned an error: {raw.get('result')}")
    meta = {"models": list((raw.get("modelUsage") or {}).keys()),
            "elapsed_seconds": round(time.time() - t0, 1),
            "cost_usd_list_price": raw.get("total_cost_usd")}
    return raw["structured_output"], meta
