#!/usr/bin/env python3
"""
Start each notion-<slug> server the way Claude Desktop will, and report what
answers.

verify.py checks the tokens. This checks the layer above: that the launcher
starts under the bare environment a GUI app gives its children, finds python3
and the recorded node without a login shell's PATH, passes its identity check,
completes an MCP handshake and lists its tools. Those fail separately. A token
can be perfect while every server dies with 'node: not found' under the app.

The command and arguments come from the desktop config entry when there is
one, otherwise from what make desktop would write. The environment is the
minimal one launchd gives an app, not this terminal's, so a pass here means
the app will work rather than that a shell does.

One tool is called per server: the read-only get-self, to confirm that the
process answering on stdio holds the token the launcher verified, not one
picked up from somewhere else after the check.
"""

import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import workspaces as ws  # noqa: E402

DESKTOP_CONFIG = Path(os.environ.get(
    "CLAUDE_DESKTOP_CONFIG",
    Path.home() / "Library" / "Application Support" / "Claude" /
    "claude_desktop_config.json",
))
TIMEOUT = 45

# Upstream derives its annotations from the HTTP method, so these two POSTs
# are marked destructive although they only read. Counted as reads here, since
# a Read content integration can use both.
POST_READS = ("post-search", "query-data-source")


def is_read(tool):
    return bool((tool.get("annotations") or {}).get("readOnlyHint")) \
        or tool["name"].endswith(POST_READS)


def app_env():
    """Roughly what a GUI app launched from the Dock passes its children."""
    env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}
    for key in ("HOME", "USER", "LOGNAME", "TMPDIR", "SHELL"):
        if key in os.environ:
            env[key] = os.environ[key]
    return env


def registered(path, key="mcpServers"):
    try:
        return json.loads(path.read_text()).get(key) or {}
    except (OSError, ValueError):
        return {}


class Server:
    def __init__(self, command, args, env):
        self.proc = subprocess.Popen(
            [command, *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, env=env, bufsize=1,
        )
        self.lines = queue.Queue()
        self.next_id = 0
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.proc.stdout:
            self.lines.put(line)
        self.lines.put(None)

    def call(self, method, params=None):
        self.next_id += 1
        request = {"jsonrpc": "2.0", "id": self.next_id, "method": method}
        if params is not None:
            request["params"] = params
        try:
            self.proc.stdin.write(json.dumps(request) + "\n")
            self.proc.stdin.flush()
        except BrokenPipeError:
            raise RuntimeError(self.death())
        while True:
            try:
                line = self.lines.get(timeout=TIMEOUT)
            except queue.Empty:
                raise RuntimeError(f"no answer to {method} within {TIMEOUT}s")
            if line is None:
                raise RuntimeError(self.death())
            try:
                message = json.loads(line)
            except ValueError:
                continue  # stray output, not protocol
            if message.get("id") == self.next_id:
                return message

    def notify(self, method):
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method}) + "\n")
        self.proc.stdin.flush()

    def death(self):
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        err = [l for l in (self.proc.stderr.read() or "").strip().splitlines() if l]
        return err[-1] if err else "server exited without output"

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()


def check(entry, command, args):
    server = Server(command, args, app_env())
    try:
        init = server.call("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "many-notions-check", "version": "1.0.0"},
        })
        if "error" in init:
            return False, f"initialize failed: {init['error']}", []
        server.notify("notifications/initialized")

        listed = server.call("tools/list")
        tools = listed.get("result", {}).get("tools", [])
        if not tools:
            return False, "no tools listed", []

        self_tool = next((t["name"] for t in tools if t["name"].endswith("get-self")), None)
        if not self_tool:
            return False, "no get-self tool to confirm the token with", tools
        answer = server.call("tools/call", {"name": self_tool, "arguments": {}})
        try:
            me = json.loads(answer["result"]["content"][0]["text"])
        except (KeyError, IndexError, ValueError, TypeError):
            return False, f"get-self returned something unexpected: {answer}", tools
        identity = ws.check_identity(entry, me)
        if not identity.ok:
            return False, f"server answers for another workspace: {identity.detail}", tools
        return True, f"get-self: '{identity.actual_name}'", tools
    except RuntimeError as exc:
        return False, str(exc), []
    finally:
        server.close()


def main():
    try:
        entries = ws.load_workspaces()
    except ws.Problem as exc:
        sys.exit(str(exc))

    desktop = registered(DESKTOP_CONFIG)
    home = os.environ.get("CLAUDE_CONFIG_DIR")
    code = registered(Path(home) / ".claude.json" if home else Path.home() / ".claude.json")

    failed = 0
    for entry in entries:
        slug, name = entry["slug"], ws.server_name(entry["slug"])
        d, c = desktop.get(name), code.get(name)
        command, args = str(ws.LAUNCHER), [slug]
        if isinstance(d, dict) and d.get("command"):
            command, args = d["command"], d.get("args") or []

        ok, detail, tools = check(entry, command, args)
        reads = sum(1 for t in tools if is_read(t))
        writes = len(tools) - reads

        print(f"{name}  ({entry['name']}, access {entry['access']})")
        print(f"  desktop  {'registered' if d else 'not registered, run make desktop'}")
        print(f"  code     {'registered' if c else 'not registered, run make code'}")
        if ok:
            print(f"  server   ok, {len(tools)} tools ({reads} read, {writes} write), {detail}")
            for tool in tools:
                kind = "read " if is_read(tool) else "write"
                print(f"             {kind}  {tool['name']}")
            if entry["access"] == "read" and writes:
                print(
                    f"  note     the {writes} write tools are listed because the "
                    "upstream server cannot hide them. Notion refuses them for "
                    "an integration with Read content only."
                )
        else:
            failed += 1
            print(f"  server   FAILED  {detail}")
        print()

    if failed:
        print(f"{failed} server(s) failed. See README.md, 'When it does not work'.",
              file=sys.stderr)
        return 1
    print("All servers answer under the app's environment.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
