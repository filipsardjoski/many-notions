#!/usr/bin/env bash
# Install the pinned Notion MCP server and the local leak-check hooks.
#
#   npm ci           installs exactly what package-lock.json names, with its
#                    integrity hashes, or fails. Never a newer version.
#   --ignore-scripts no dependency gets to run code at install time. Nothing in
#                    this tree needs a build step: the upstream package ships
#                    a prebuilt bin/cli.mjs.
#
# Records the absolute path of the node that ran the install in .node-path, so
# the launcher can exec the same one under Claude Desktop, whose PATH does not
# reliably include Homebrew. The path is not resolved through symlinks, so a
# `brew upgrade node` keeps working.
#
# Never touches the Keychain. Tokens are stored by `make token`.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

node_bin="$(command -v node || true)"
if [[ -z "$node_bin" ]]; then
  echo "node is not on PATH. Install it (brew install node) and run this again." >&2
  exit 1
fi
command -v npm >/dev/null 2>&1 || { echo "npm is not on PATH." >&2; exit 1; }

major="$("$node_bin" -p 'process.versions.node.split(".")[0]')"
if (( major < 20 )); then
  echo "node $("$node_bin" --version) is too old. The upstream server needs 20 or later." >&2
  exit 1
fi

echo "Installing the pinned Notion MCP server (npm ci)"
npm ci --ignore-scripts --no-fund

printf '%s\n' "$node_bin" > .node-path
echo "  node        $node_bin ($("$node_bin" --version)), recorded in .node-path"

python3 - <<'EOF'
import sys
sys.path.insert(0, "lib")
import workspaces as ws
installed, pinned = ws.upstream_version(), ws.pinned_version()
if installed != pinned:
    sys.exit(f"installed {installed} but package.json pins {pinned}")
ws.upstream_cli()
print(f"  upstream    @notionhq/notion-mcp-server {installed}, exact pin")
EOF

chmod +x bin/notion-mcp hooks/* scripts/*.sh scripts/*.py

# Tracked hooks, so the leak check is the same for every clone. core.hooksPath
# is local config, which is why install has to set it.
if git rev-parse --git-dir >/dev/null 2>&1; then
  git config core.hooksPath hooks
  echo "  git hooks   pre-commit and commit-msg run scripts/check-leaks.py"
fi

if [[ ! -f workspaces.yml ]]; then
  echo
  echo "No workspaces.yml yet:"
  echo "  cp workspaces.example.yml workspaces.yml"
  echo "  \$EDITOR workspaces.yml"
fi

echo
echo "Next: make token SLUG=<slug>, once per workspace"
