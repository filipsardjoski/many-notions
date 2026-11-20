"""
Shared by bin/notion-mcp and every script under scripts/.

Standard library only, and Python 3.9 compatible, because Claude Desktop starts
the launcher with whatever python3 a GUI app finds on a bare PATH, which on
macOS is /usr/bin/python3. A dependency that has to be found on PATH by a
child process the app starts is a worse problem than a hundred lines of
manifest parsing.

Nothing in this module prints or logs a token. Functions that hold one return
it to the caller or put it into a child's environment, and every exception
raised here carries a message that is safe to print as is.
"""

import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "workspaces.yml"
EXAMPLE = ROOT / "workspaces.example.yml"
LAUNCHER = ROOT / "bin" / "notion-mcp"

# Written by `make install`: the absolute path of the node that ran `npm ci`.
# Claude Desktop starts servers with a PATH that does not reliably include
# Homebrew, so the launcher cannot look node up at runtime and expect to find
# the same one.
NODE_PATH_FILE = ROOT / ".node-path"
UPSTREAM_DIR = ROOT / "node_modules" / "@notionhq" / "notion-mcp-server"
UPSTREAM_CLI = UPSTREAM_DIR / "bin" / "cli.mjs"

KEYCHAIN_SERVICE = "many-notions"
SECURITY = "/usr/bin/security"

API = "https://api.notion.com/v1"
NOTION_VERSION = "2025-09-03"
TIMEOUT = 20

SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
ACCESS_LEVELS = ("read", "write")
DEFAULT_ACCESS = "read"
REQUIRED = ("slug", "name")
KNOWN_KEYS = ("slug", "name", "access", "workspace_id")

# The parent environment variables a server is allowed to inherit. Everything
# else is dropped on purpose, and three of the dropped ones matter:
#
#   OPENAPI_MCP_HEADERS  the upstream server prefers it over NOTION_TOKEN, so a
#                        stray value would swap the verified token for another
#                        one after the identity check had already passed
#   BASE_URL             the upstream server sends every request, token
#                        included, to this host instead of api.notion.com
#   NODE_OPTIONS         can --require arbitrary code into the server process
#
# The proxy variables stay so the server can reach Notion on a network that
# needs one; the identity check in this module honours the same variables, so
# both see the same route.
PASS_THROUGH = (
    "HOME", "USER", "LOGNAME", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE",
    "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy",
    "NO_PROXY", "no_proxy",
)


class Problem(Exception):
    """An error whose message is safe to show the user. Never holds a token."""


class TokenMissing(Problem):
    pass


class TokenRejected(Problem):
    pass


# --- manifest ---------------------------------------------------------------

def parse_manifest(text):
    """Minimal reader for the flat list-of-maps in workspaces.yml."""
    entries, current = [], None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        stripped = line.strip()
        if not stripped or stripped == "workspaces:":
            continue
        if stripped.startswith("- "):
            if current is not None:
                entries.append(current)
            current = {}
            stripped = stripped[2:].strip()
        if current is None or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        current[key.strip()] = value
    if current is not None:
        entries.append(current)
    for entry in entries:
        entry["access"] = (entry.get("access") or DEFAULT_ACCESS).strip().lower()
    return entries


def validate(entries):
    errors = []
    seen = set()
    for i, entry in enumerate(entries, 1):
        for field in REQUIRED:
            if not entry.get(field):
                errors.append(f"workspace {i}: missing '{field}'")
        slug = entry.get("slug", "")
        if slug and not SLUG_RE.match(slug):
            errors.append(
                f"workspace {i}: slug {slug!r} is invalid. Use lowercase "
                "letters, digits and single hyphens; it becomes the server "
                "name notion-<slug> and the Keychain account name."
            )
        if slug in seen:
            errors.append(f"workspace {i}: duplicate slug {slug!r}")
        seen.add(slug)
        if entry.get("access") not in ACCESS_LEVELS:
            errors.append(
                f"workspace {i}: access must be read or write, "
                f"not {entry.get('access')!r}"
            )
        for key in entry:
            if key not in KNOWN_KEYS:
                errors.append(
                    f"workspace {i}: unknown key {key!r}. Known keys: "
                    + ", ".join(KNOWN_KEYS)
                )
    return errors


def load_workspaces(path=MANIFEST):
    if not path.exists():
        raise Problem(
            f"missing {path}\n\n"
            "    cp workspaces.example.yml workspaces.yml\n"
            "    $EDITOR workspaces.yml\n"
        )
    entries = parse_manifest(path.read_text())
    if not entries:
        raise Problem(f"{path.name} has no workspaces")
    errors = validate(entries)
    if errors:
        raise Problem(f"{path.name} is not valid:\n  " + "\n  ".join(errors))
    return entries


def find(entries, slug):
    for entry in entries:
        if entry["slug"] == slug:
            return entry
    return None


def server_name(slug):
    return f"notion-{slug}"


# --- node and the upstream server -------------------------------------------

def node_binary():
    if not NODE_PATH_FILE.exists():
        raise Problem("node has not been recorded yet. Run: make install")
    node = NODE_PATH_FILE.read_text().strip()
    if not node or not os.access(node, os.X_OK):
        raise Problem(
            f"the node recorded by make install ({node or 'empty'}) is gone. "
            "Run: make install"
        )
    return node


def upstream_cli():
    if not UPSTREAM_CLI.exists():
        raise Problem(
            f"the pinned Notion MCP server is not installed at {UPSTREAM_DIR}. "
            "Run: make install"
        )
    return UPSTREAM_CLI


def upstream_version():
    try:
        return json.loads((UPSTREAM_DIR / "package.json").read_text())["version"]
    except (OSError, ValueError, KeyError):
        return None


def pinned_version():
    try:
        deps = json.loads((ROOT / "package.json").read_text())["dependencies"]
        return deps["@notionhq/notion-mcp-server"]
    except (OSError, ValueError, KeyError):
        return None


def child_env(token, node):
    """
    The complete environment for the upstream server: an allowlist of the
    parent's variables, a PATH that holds the recorded node, and the token.
    Built from scratch rather than copied, for the reasons on PASS_THROUGH.
    """
    env = {k: os.environ[k] for k in PASS_THROUGH if k in os.environ}
    env["PATH"] = os.pathsep.join([str(Path(node).parent), "/usr/bin", "/bin"])
    env["NOTION_TOKEN"] = token
    return env


# --- Keychain ---------------------------------------------------------------

def read_token(slug):
    """
    The token for one slug, from the login Keychain. The service is fixed and
    the account is the slug, so this can only ever return the item for the
    workspace it was asked about. There is no search and no fallback.
    """
    try:
        proc = subprocess.run(
            [SECURITY, "find-generic-password",
             "-s", KEYCHAIN_SERVICE, "-a", slug, "-w"],
            capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        raise Problem(
            "timed out waiting for the Keychain. If macOS showed a prompt, "
            "choose Always Allow and start the server again."
        )
    if proc.returncode == 44:
        raise TokenMissing(
            f"no token in the Keychain for '{slug}' "
            f"(service {KEYCHAIN_SERVICE}, account {slug}). "
            f"Run: make token SLUG={slug}"
        )
    if proc.returncode != 0:
        # stderr from security never contains the secret, only the reason.
        reason = (proc.stderr or "").strip().splitlines()
        raise Problem(
            f"the Keychain refused to return the token for '{slug}' "
            f"(exit {proc.returncode}: {reason[-1] if reason else 'no detail'}). "
            "If you chose Deny at a Keychain prompt, start the server again "
            "and choose Always Allow."
        )
    token = proc.stdout.rstrip("\r\n")
    if not token:
        raise TokenMissing(
            f"the Keychain item for '{slug}' is empty. Run: make token SLUG={slug}"
        )
    return token


def delete_token(slug):
    proc = subprocess.run(
        [SECURITY, "delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", slug],
        capture_output=True, text=True,
    )
    return proc.returncode == 0


# --- Notion API -------------------------------------------------------------

def api(token, method, path, body=None):
    """
    One Notion API call. Returns the decoded JSON body. Raises TokenRejected on
    401 and Problem on anything else that is not a 2xx, with Notion's own error
    code and message, which never echo the token back.
    """
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        API + path, data=data, method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
            "User-Agent": "many-notions",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode())
        except ValueError:
            detail = {}
        code = detail.get("code", "")
        message = detail.get("message", exc.reason)
        if exc.code == 401:
            raise TokenRejected(
                f"Notion rejected the token ({code or 'unauthorized'}). It was "
                "revoked, regenerated, or is not an integration secret."
            )
        raise Problem(f"Notion returned HTTP {exc.code} for {path}: {code} {message}")
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise Problem(f"could not reach api.notion.com ({reason})")
    except ValueError:
        raise Problem(f"Notion returned something that is not JSON for {path}")


def whoami(token):
    return api(token, "GET", "/users/me")


def search(token, page_size=10):
    """One search, no query. Returns (results visible, more available)."""
    body = api(token, "POST", "/search", {"page_size": page_size})
    return len(body.get("results") or []), bool(body.get("has_more"))


def workspace_of(me):
    """(workspace id or None, workspace name or None, bot id) from /users/me."""
    bot = me.get("bot") or {}
    wid = bot.get("workspace_id") or None
    wname = bot.get("workspace_name") or None
    return wid, wname, me.get("id")


def norm_id(value):
    return (value or "").replace("-", "").strip().lower()


def norm_name(value):
    return " ".join((value or "").split()).casefold()


class Identity:
    """
    The outcome of comparing /users/me against a manifest entry.

    ok is True only on a positive match. Anything the check could not decide
    is a refusal, never a pass: an unverifiable server is the same risk as a
    mismatched one, because it serves whatever the token reaches under a name
    that promises something specific.
    """

    def __init__(self, ok, detail, actual_name=None, actual_id=None,
                 bot_id=None, by=None, note=None):
        self.ok = ok
        self.detail = detail
        self.actual_name = actual_name
        self.actual_id = actual_id
        self.bot_id = bot_id
        self.by = by
        self.note = note


def check_identity(entry, me):
    expected_name = entry["name"]
    expected_id = entry.get("workspace_id")
    wid, wname, bot_id = workspace_of(me)

    if me.get("type") != "bot":
        return Identity(
            False,
            "the token does not belong to an integration (Notion reported a "
            f"{me.get('type') or 'missing'} user, not a bot)",
            bot_id=bot_id,
        )

    note = None
    if expected_id and wid:
        if norm_id(expected_id) == norm_id(wid):
            return Identity(True, "workspace id matches", wname, wid, bot_id, "id")
        return Identity(
            False,
            f"expected workspace id {expected_id}, the token belongs to {wid} "
            f"({wname or 'name not reported'})",
            wname, wid, bot_id,
        )
    if expected_id and not wid:
        note = (
            "workspaces.yml pins a workspace_id, but Notion did not return one "
            "for this token, so the name was compared instead"
        )

    if not wname:
        return Identity(
            False,
            "Notion returned neither a workspace id nor a workspace name for "
            "this token, so the workspace cannot be confirmed",
            None, wid, bot_id,
        )
    if norm_name(wname) != norm_name(expected_name):
        return Identity(
            False,
            f"expected workspace '{expected_name}', the token belongs to '{wname}'",
            wname, wid, bot_id,
        )
    return Identity(True, "workspace name matches", wname, wid, bot_id, "name", note)
