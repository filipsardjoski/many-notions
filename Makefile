.PHONY: help install token verify desktop code check-mcp check-leaks

SLUG ?=

help:
	@echo "make install          npm ci the pinned Notion MCP server, set up git hooks"
	@echo "make token SLUG=x     store workspace x's token in the Keychain (typed, never an argument)"
	@echo "make verify           check each token's workspace, and one search per workspace"
	@echo "make desktop          register one notion-<slug> server per workspace with Claude Desktop"
	@echo "make code             register the same servers with Claude Code, user scope"
	@echo "make check-mcp        start each server the way Claude Desktop will and list its tools"
	@echo "make check-leaks      refuse real workspace data or tokens in publishable files"
	@echo ""
	@echo "First run:  cp workspaces.example.yml workspaces.yml && \$$EDITOR workspaces.yml"
	@echo "            make install && make token SLUG=<slug> && make verify"
	@echo "            make desktop && make code"

install:
	@bash scripts/install.sh

# The token is typed at a hidden prompt opened by /usr/bin/security. SLUG is
# the only thing on this command line, so nothing secret reaches shell history.
token:
	@bash scripts/token.sh "$(SLUG)"

verify:
	@python3 scripts/verify.py

# Claude Desktop reads claude_desktop_config.json once at launch. This merges
# one entry per workspace into it, backing it up first, and leaves every entry
# it did not create alone.
desktop:
	@python3 scripts/gen-desktop.py

code:
	@python3 scripts/gen-code.py

check-mcp:
	@python3 scripts/check-mcp.py

# Also runs on every commit, against the staged tree, through hooks/pre-commit.
check-leaks:
	@python3 scripts/check-leaks.py
