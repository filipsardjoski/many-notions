#!/usr/bin/env python3
"""
Register one notion-<slug> MCP server per workspace with Claude Desktop.

    python3 scripts/gen-desktop.py            merge into the desktop config
    python3 scripts/gen-desktop.py --print    show the entries, write nothing

Each entry is the launcher and a slug, nothing else:

    "notion-acme": {"command": "/abs/path/bin/notion-mcp", "args": ["acme"]}

No token and no environment go into the file. The launcher reads the token
from the Keychain at startup, so the config the app keeps in plain JSON never
holds a secret.

Merges rather than overwrites. The app keeps preferences and other servers in
the same file, and this only ever adds, updates or removes entries that run
this repo's bin/notion-mcp. A notion-* entry pointing anywhere else, or any
other server (gmail-*, tetris, ...), is left exactly as it was, and a name
collision with one of those is reported rather than resolved.

Backs the file up before writing, to a timestamped name of its own so it never
replaces a backup another tool made, and writes through a temp file, because
a half-written config stops the app launching.
"""

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import workspaces as ws  # noqa: E402

DEFAULT_CONFIG = (
    Path.home() / "Library" / "Application Support" / "Claude" /
    "claude_desktop_config.json"
)
CONFIG = Path(os.environ.get("CLAUDE_DESKTOP_CONFIG", DEFAULT_CONFIG))


def ours(entry):
    return isinstance(entry, dict) and entry.get("command") == str(ws.LAUNCHER)


def wanted(entries):
    return {
        ws.server_name(e["slug"]): {
            "command": str(ws.LAUNCHER),
            "args": [e["slug"]],
        }
        for e in entries
    }


def read_config():
    if not CONFIG.exists():
        return {}
    try:
        return json.loads(CONFIG.read_text())
    except ValueError as exc:
        sys.exit(
            f"{CONFIG} is not valid JSON ({exc}).\n"
            "Refusing to overwrite a file that may hold settings this script "
            "cannot reconstruct. Repair it, then run this again."
        )


def merge(config, want):
    """Returns (added, updated, unchanged, removed, blocked)."""
    servers = config.setdefault("mcpServers", {})
    added, updated, unchanged, removed, blocked = [], [], [], [], []

    for name in [n for n, e in servers.items()
                 if n.startswith("notion-") and n not in want and ours(e)]:
        del servers[name]
        removed.append(name)

    for name, entry in want.items():
        current = servers.get(name)
        if current is None:
            servers[name] = entry
            added.append(name)
        elif not ours(current):
            blocked.append(name)
        elif current == entry:
            unchanged.append(name)
        else:
            servers[name] = entry
            updated.append(name)
    return added, updated, unchanged, removed, blocked


def write_config(config):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    if CONFIG.exists():
        stamp = time.strftime("%Y%m%dT%H%M%S")
        backup = CONFIG.with_name(f"{CONFIG.name}.bak-many-notions-{stamp}")
        shutil.copy2(CONFIG, backup)
        print(f"  backed up   {backup}")
    fd, tmp = tempfile.mkstemp(dir=str(CONFIG.parent), suffix=".json")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(config, fh, indent=2)
            fh.write("\n")
        os.replace(tmp, CONFIG)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def main(argv):
    try:
        entries = ws.load_workspaces()
    except ws.Problem as exc:
        sys.exit(str(exc))
    want = wanted(entries)

    if "--print" in argv:
        print(json.dumps({"mcpServers": want}, indent=2))
        return 0

    config = read_config()
    added, updated, unchanged, removed, blocked = merge(config, want)

    if added or updated or removed:
        write_config(config)
        print(f"Updated {CONFIG}")
    else:
        print(f"No change to {CONFIG}")

    for entry in entries:
        name = ws.server_name(entry["slug"])
        state = (
            "added" if name in added else
            "updated" if name in updated else
            "unchanged" if name in unchanged else
            "NOT WRITTEN"
        )
        print(f"  {name:<24} {state:<12} {entry['name']} ({entry['access']})")
    for name in removed:
        print(f"  {name:<24} removed      no longer in workspaces.yml")

    others = sorted(n for n, e in config.get("mcpServers", {}).items()
                    if n not in want and not ours(e))
    if others:
        print(f"\nLeft alone: {', '.join(others)}")

    if blocked:
        print(
            "\nNot written, because an entry with the same name already exists "
            "and does not run this repo's launcher:\n  " + "\n  ".join(blocked)
            + "\nRename or remove it in the desktop config yourself, then rerun.",
            file=sys.stderr,
        )
        return 1

    if added or updated or removed:
        print("\nQuit Claude Desktop completely (Cmd-Q) and reopen it to pick these up.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
