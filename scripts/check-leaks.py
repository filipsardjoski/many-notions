#!/usr/bin/env python3
"""
Refuse to publish this repo with real workspace data or a token in it.

workspaces.yml and the generated files are gitignored, which handles the
obvious leak. It does nothing for the easier one: a real workspace name typed
into a comment, a page URL pasted into the README while explaining setup, an
absolute path out of a generated config, or a token pasted while debugging.
A force-push does not reliably take any of those back out of a clone someone
already made, so this runs before the commit exists.

Passes, from precise to broad:

  1. The manifest. Every slug, workspace name and workspace_id in
     workspaces.yml, in the spellings a leak would use.
  2. Your own words. One per line in .leak-words, gitignored, for names the
     manifest does not hold yet: another workspace, an organisation, a person.
  3. This machine. Your login name, which is what an absolute path out of a
     generated config carries.
  4. Shapes. Anything that looks like a Notion token, a Notion page or
     workspace id, a Notion page URL, or an email address on a domain not
     reserved for documentation. These catch what no list knew about.

Passes 1 to 3 skip name-bearing files such as LICENSE, whose job is to name the
copyright holder. Pass 4 applies everywhere.

    python3 scripts/check-leaks.py               what `git add -A` would publish
    python3 scripts/check-leaks.py --staged      the index, as pre-commit sees it
    python3 scripts/check-leaks.py --message F   a commit message (commit-msg)
    python3 scripts/check-leaks.py --list        what it is looking for

Output quotes what it found, so it can print a real name back at you. That is
the point, and the reason this is a local check rather than CI output.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "workspaces.yml"
WORDS = ROOT / ".leak-words"

# A Notion integration secret: the current prefix and the legacy one, then a
# long run of alphanumerics. Written as an alternation so that this file does
# not itself contain the bare prefix it is looking for.
TOKEN = re.compile(r"\b(?:ntn|secret)_[A-Za-z0-9]{20,}")

UUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
HEX32 = re.compile(r"(?<![0-9A-Za-z])[0-9a-fA-F]{32}(?![0-9A-Za-z])")
PLACEHOLDER_IDS = {"0" * 32}

NOTION_URL = re.compile(r"https?://(?:[A-Za-z0-9-]+\.)?notion\.(?:so|site)(/[^\s)\"'<>`]*)?")
NOTION_PATHS_OK = ("", "/", "/profile/integrations", "/my-integrations", "/help")

ADDRESS = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
RESERVED_SUFFIXES = (
    ".test", ".example", ".invalid", ".localhost",
    "example.com", "example.net", "example.org",
)
# Not anybody's private address: the commit trailer for AI co-authorship and
# GitHub's per-user noreply domain.
ALLOWED_ADDRESSES = {"noreply@anthropic.com"}
ALLOWED_DOMAINS = ("users.noreply.github.com",)

# Too generic to identify anyone. A real slug or name like a company or
# community is identifying; "personal" must not fail the build.
GENERIC = {
    "personal", "work", "team", "main", "home", "test", "notes", "wiki",
    "docs", "acme", "side-project", "company", "school", "private", "shared",
}
MIN_LEN = 4

NAME_BEARING = {"LICENSE", "LICENCE", "COPYING", "COPYRIGHT", "AUTHORS", "NOTICE"}
SKIP_SUFFIXES = (
    ".png", ".jpg", ".jpeg", ".gif", ".pdf", ".ico", ".zip", ".gz",
    ".pyc", ".woff", ".woff2", ".ttf",
)


def name_bearing(name):
    return os.path.basename(name).split(".", 1)[0].upper() in NAME_BEARING


def manifest_strings():
    """(string, why) for every identifying value in workspaces.yml."""
    if not MANIFEST.exists():
        return []
    out = {}
    for raw in MANIFEST.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if line.startswith("- "):
            line = line[2:].strip()
        if ":" not in line:
            continue
        key, value = (s.strip() for s in line.split(":", 1))
        value = value.strip("'\"").strip()
        if not value:
            continue
        low = value.lower()
        if key == "slug" and low not in GENERIC and len(low) >= MIN_LEN:
            out[low] = "a slug from workspaces.yml"
        elif key == "name" and low not in GENERIC and len(low) >= MIN_LEN:
            out[low] = "a workspace name from workspaces.yml"
            squashed = "".join(low.split())
            if squashed != low:
                out[squashed] = "a workspace name from workspaces.yml, spaces removed"
        elif key == "workspace_id":
            out[low] = "a workspace id from workspaces.yml"
            out[low.replace("-", "")] = "a workspace id from workspaces.yml, no dashes"
    return sorted(out.items())


def local_words():
    if not WORDS.exists():
        return []
    words = []
    for raw in WORDS.read_text().splitlines():
        word = raw.split("#", 1)[0].strip().lower()
        if word:
            words.append((word, "a word from .leak-words"))
    return words


def machine_strings():
    login = (os.environ.get("USER") or os.environ.get("LOGNAME") or "").lower()
    if len(login) >= MIN_LEN and login not in GENERIC:
        return [(login, "your login name, as in an absolute home path")]
    return []


def git(*args):
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, check=True,
    ).stdout


def worktree_files():
    """Everything `git add -A` would stage. Ignored files hold the real data
    on purpose, so they are not scanned."""
    for name in git("ls-files", "-co", "--exclude-standard", "-z").decode().split("\0"):
        path = ROOT / name
        if name and path.is_file() and not name.lower().endswith(SKIP_SUFFIXES):
            try:
                yield name, path.read_bytes()
            except OSError:
                continue


def staged_files():
    """Every path in the index with its staged content: the tree the commit
    will record, which can differ from the working copy."""
    for record in git("ls-files", "-s", "-z").decode().split("\0"):
        if not record:
            continue
        meta, name = record.split("\t", 1)
        mode, sha = meta.split()[:2]
        if mode == "160000" or name.lower().endswith(SKIP_SUFFIXES):
            continue
        yield name, git("cat-file", "blob", sha)


def reserved(address):
    domain = address.rpartition("@")[2].lower()
    if domain.endswith(ALLOWED_DOMAINS):
        return True
    return any(domain == s.lstrip(".") or domain.endswith(s) for s in RESERVED_SUFFIXES)


def scan(name, text, personal):
    found = []
    lines = text.splitlines()
    lowered = text.lower()

    for value, why in ([] if name_bearing(name) else personal):
        if value in lowered:
            for n, line in enumerate(lines, 1):
                if value in line.lower():
                    found.append((name, n, value, why))

    for n, line in enumerate(lines, 1):
        for m in TOKEN.finditer(line):
            found.append((name, n, m.group()[:8] + "...", "a Notion token"))
        for m in UUID.finditer(line):
            if m.group().replace("-", "") not in PLACEHOLDER_IDS:
                found.append((name, n, m.group(), "a Notion-style id"))
        for m in HEX32.finditer(line):
            if m.group().lower() not in PLACEHOLDER_IDS:
                found.append((name, n, m.group(), "a Notion-style id"))
        for m in NOTION_URL.finditer(line):
            path = (m.group(1) or "").rstrip(".,;:")
            if path not in NOTION_PATHS_OK:
                found.append((name, n, m.group(), "a Notion page or workspace URL"))
        for m in ADDRESS.finditer(line):
            address = m.group()
            if address.lower() not in ALLOWED_ADDRESSES and not reserved(address):
                found.append((name, n, address, "an email address on a real domain"))
    return found


def main(argv):
    personal, seen_values = [], set()
    for value, why in manifest_strings() + local_words() + machine_strings():
        if value not in seen_values:
            seen_values.add(value)
            personal.append((value, why))

    if "--list" in argv:
        print("strings that must not appear (skipped in LICENSE and similar):")
        for value, why in personal:
            print(f"  {value}   ({why})")
        print("\nshapes refused everywhere: Notion tokens, Notion ids, Notion page")
        print("URLs, and email addresses outside these domains:")
        for s in RESERVED_SUFFIXES + ALLOWED_DOMAINS:
            print(f"  *{s}" if s.startswith(".") else f"  {s}")
        return 0

    if "--message" in argv:
        i = argv.index("--message")
        path = Path(argv[i + 1])
        # Comment lines are stripped by git before the message is stored.
        text = "\n".join(l for l in path.read_text().splitlines() if not l.startswith("#"))
        sources = [("commit message", text.encode())]
        what = "the commit message"
    elif "--staged" in argv:
        sources = list(staged_files())
        what = f"{len(sources)} staged files"
    else:
        sources = list(worktree_files())
        what = f"{len(sources)} publishable files"

    failures = []
    for name, data in sources:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        failures.extend(scan(name, text, personal))

    if not failures:
        print(f"check-leaks: nothing private in {what}")
        return 0

    print(f"check-leaks: FAIL, private data in {what}\n", file=sys.stderr)
    seen = set()
    for name, n, value, why in failures:
        if (name, n, value) in seen:
            continue
        seen.add((name, n, value))
        print(f"  {name}:{n}\n    {value}  ({why})", file=sys.stderr)
    print(
        "\nUse placeholders instead: slugs like acme and side-project, an id of\n"
        "all zeros, addresses under example.com or .test, paths under /Users/you.\n"
        "Real values belong in workspaces.yml, which is gitignored. Run\n"
        "'python3 scripts/check-leaks.py --list' to see what is checked.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
