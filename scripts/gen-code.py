#!/usr/bin/env python3
"""
Register one notion-<slug> MCP server per workspace with Claude Code, at user
scope, so every project sees them.

For each workspace this runs

    claude mcp add --scope user notion-<slug> -- <repo>/bin/notion-mcp <slug>

Same ownership rule as make desktop: an existing entry is replaced only if it
already runs this repo's launcher, entries this tool made for slugs that are
gone are removed, and anything else is left alone and reported. The current
state is read from Claude Code's own config file, read-only; every change goes
through the claude CLI.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import workspaces as ws  # noqa: E402


def user_config():
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(base) / ".claude.json" if base else Path.home() / ".claude.json"


def current_servers():
    path = user_config()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text()).get("mcpServers") or {}
    except ValueError:
        sys.exit(f"{path} is not valid JSON; fix Claude Code's config first")


def ours(entry):
    return isinstance(entry, dict) and entry.get("command") == str(ws.LAUNCHER)


def claude(*args):
    proc = subprocess.run([CLAUDE, *args], capture_output=True, text=True)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        sys.exit(f"claude {' '.join(args)} failed: {detail}")


def main():
    try:
        entries = ws.load_workspaces()
    except ws.Problem as exc:
        sys.exit(str(exc))

    servers = current_servers()
    want = {ws.server_name(e["slug"]): e for e in entries}
    blocked = []

    for name, entry in sorted(servers.items()):
        if name.startswith("notion-") and name not in want and ours(entry):
            claude("mcp", "remove", "--scope", "user", name)
            print(f"  {name:<24} removed      no longer in workspaces.yml")

    for name, entry in want.items():
        args = [entry["slug"]]
        current = servers.get(name)
        if current is not None and not ours(current):
            blocked.append(name)
            print(f"  {name:<24} NOT WRITTEN  an entry of that name runs something else")
            continue
        if current is not None and current.get("args") == args:
            print(f"  {name:<24} unchanged    {entry['name']} ({entry['access']})")
            continue
        if current is not None:
            claude("mcp", "remove", "--scope", "user", name)
        claude("mcp", "add", "--scope", "user", name, "--", str(ws.LAUNCHER), *args)
        state = "updated" if current is not None else "added"
        print(f"  {name:<24} {state:<12} {entry['name']} ({entry['access']})")

    if blocked:
        print(
            "\nRemove or rename those entries yourself (claude mcp remove "
            "--scope user <name>), then rerun.",
            file=sys.stderr,
        )
        return 1
    print("\nNew Claude Code sessions pick these up. Check with: claude mcp list")
    return 0


CLAUDE = shutil.which("claude")
if __name__ == "__main__":
    if not CLAUDE:
        sys.exit("the claude CLI is not on PATH")
    sys.exit(main())
