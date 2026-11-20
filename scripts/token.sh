#!/usr/bin/env bash
# Store one workspace's Notion token in the macOS Keychain.
#
#   make token SLUG=acme
#
# The token is typed at a hidden prompt that /usr/bin/security opens itself:
# `-w` as the last option, with no value, asks for the password interactively.
# It is never an argument, so it never reaches shell history or `ps`, and it is
# never piped, so it never sits in a file or a here-string either. That is also
# why this refuses to run without a terminal.
#
# Service many-notions, account <slug>. `-U` replaces an existing item for the
# same slug, so rotating a token is the same command.
#
# Straight after storing, the token is checked against workspaces.yml. A token
# Notion rejects, or one that belongs to a different workspace, is deleted
# again: the launcher would refuse it anyway, and leaving it there only means a
# confusing failure later. A check that cannot decide (no network, say) keeps
# the token and says so.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE="many-notions"
slug="${1:-}"

if [[ -z "$slug" ]]; then
  echo "usage: make token SLUG=<slug>   (a slug from workspaces.yml)" >&2
  exit 2
fi

# Validate the slug against the manifest before touching the Keychain, and get
# the workspace name to show at the prompt.
name="$(cd "$REPO" && python3 -c '
import sys
sys.path.insert(0, "lib")
import workspaces as ws
try:
    entries = ws.load_workspaces()
except ws.Problem as exc:
    sys.exit(str(exc))
entry = ws.find(entries, sys.argv[1])
if entry is None:
    sys.exit("no workspace %r in workspaces.yml (known: %s)" % (
        sys.argv[1], ", ".join(e["slug"] for e in entries)))
print(entry["name"] + "\t" + entry["access"])
' "$slug")"
access="${name#*$'\t'}"
name="${name%%$'\t'*}"

if [[ ! -t 0 || ! -t 1 ]]; then
  echo "make token needs an interactive terminal. The token is typed at a hidden" >&2
  echo "prompt; it is never accepted as an argument or from a pipe." >&2
  exit 2
fi

cat <<EOF
Storing the Notion token for '$slug'
  workspace   $name
  access      $access
  keychain    service $SERVICE, account $slug

Paste the integration's Internal Integration Secret at the prompt below.
Nothing is echoed, and security asks for it twice.

EOF

/usr/bin/security add-generic-password -U \
  -s "$SERVICE" -a "$slug" \
  -l "$SERVICE: $slug" \
  -D "Notion integration token" \
  -j "Written by make token in many-notions. One workspace per item." \
  -w

echo
echo "Stored. Checking which workspace it belongs to..."

set +e
python3 "$REPO/scripts/verify.py" --identity-only --slug "$slug"
rc=$?
set -e

case $rc in
  0)
    echo
    echo "Next: share the root pages with the integration (page menu, Connections),"
    echo "then run make verify."
    ;;
  3)
    /usr/bin/security delete-generic-password -s "$SERVICE" -a "$slug" >/dev/null 2>&1 || true
    echo >&2
    echo "Deleted the token again: it can never pass the startup check for '$slug'." >&2
    echo "Copy the secret from the integration created in '$name' and rerun" >&2
    echo "  make token SLUG=$slug" >&2
    exit 1
    ;;
  *)
    echo >&2
    echo "Could not confirm the workspace, so the token was kept. Run make verify" >&2
    echo "once the problem above is fixed." >&2
    exit 1
    ;;
esac
