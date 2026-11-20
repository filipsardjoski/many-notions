#!/usr/bin/env python3
"""
Confirm each workspace's token belongs to the workspace workspaces.yml claims,
and that the integration can see something.

    python3 scripts/verify.py                         every workspace
    python3 scripts/verify.py --slug acme             one
    python3 scripts/verify.py --slug acme --identity-only
                                                      identity, no search
                                                      (make token uses this)

Per workspace: the Keychain item exists, GET /v1/users/me reports the expected
workspace (by id when the entry pins one and Notion returns one, otherwise by
name), and one search returns at least one result. A search that returns
nothing means no page has been shared with the integration yet, which is a
setup step, not a broken token, but it leaves the server useless, so it fails.

Also catches two slugs holding the same token, which a per-slug check cannot
see on its own: each passes, and one of them is serving the other's workspace.

Exit codes: 0 all good, 3 a definite mismatch or rejected token (make token
deletes the item on this), 1 anything else, including checks that could not
decide.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import workspaces as ws  # noqa: E402

DEFINITE = 3


def row(*cols):
    print("{:<18} {:<7} {:<26} {}".format(*cols))


def main(argv):
    only = None
    if "--slug" in argv:
        i = argv.index("--slug")
        if i + 1 >= len(argv):
            sys.exit("--slug needs a value")
        only = argv[i + 1]
    identity_only = "--identity-only" in argv

    try:
        entries = ws.load_workspaces()
    except ws.Problem as exc:
        print(exc, file=sys.stderr)
        return 1
    if only:
        entry = ws.find(entries, only)
        if entry is None:
            print(f"no workspace {only!r} in workspaces.yml", file=sys.stderr)
            return 1
        entries = [entry]

    row("SLUG", "ACCESS", "EXPECTED", "RESULT")
    row("-" * 18, "-" * 7, "-" * 26, "-" * 30)

    worst = 0
    hints = []
    bots = {}

    for entry in entries:
        slug, expected, access = entry["slug"], entry["name"], entry["access"]

        def fail(result, code=1):
            nonlocal worst
            row(slug, access, expected, result)
            worst = max(worst, code)

        try:
            token = ws.read_token(slug)
        except ws.TokenMissing:
            fail(f"NO TOKEN  run make token SLUG={slug}")
            continue
        except ws.Problem as exc:
            fail(f"KEYCHAIN  {exc}")
            continue

        try:
            me = ws.whoami(token)
        except ws.TokenRejected as exc:
            fail(f"REJECTED  {exc}", DEFINITE)
            continue
        except ws.Problem as exc:
            fail(f"UNKNOWN  {exc}")
            continue

        identity = ws.check_identity(entry, me)
        if not identity.ok:
            # Only a positive answer about a different workspace is definite.
            # "Could not tell" stays at 1, so make token keeps the item.
            code = DEFINITE if identity.actual_name or identity.actual_id else 1
            fail(f"MISMATCH  {identity.detail}", code)
            continue

        if identity.bot_id in bots:
            fail(f"DUPLICATE  same token as '{bots[identity.bot_id]}'", DEFINITE)
            continue
        bots[identity.bot_id] = slug

        result = f"'{identity.actual_name}' by {identity.by}"
        if identity.note:
            hints.append(f"{slug}: {identity.note}")
        if identity.actual_id and not entry.get("workspace_id"):
            hints.append(
                f"{slug}: Notion reports workspace id {identity.actual_id}. Add "
                f"'workspace_id: {identity.actual_id}' to this entry in "
                "workspaces.yml to check by id rather than by name."
            )

        if not identity_only:
            try:
                count, more = ws.search(token)
            except ws.Problem as exc:
                fail(f"{result}, SEARCH FAILED  {exc}")
                continue
            if count == 0:
                fail(
                    f"{result}, NOTHING SHARED  share root pages with the "
                    "integration: page menu, Connections"
                )
                continue
            result += f", search sees {count}{'+' if more else ''}"

        row(slug, access, expected, result)

    print()
    for hint in hints:
        print(f"note  {hint}")
    if hints:
        print()

    sys.stdout.flush()
    if worst == 0:
        print("All workspaces verified." if not identity_only else "Identity verified.")
    else:
        print("Problems found. See README.md, 'When it does not work'.", file=sys.stderr)
    return worst


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
