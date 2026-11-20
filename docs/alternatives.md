# Alternatives considered

Written down so a future decision to switch starts from evidence rather than
from scratch. Evaluated November 2026.

The constraint that shapes all of it: the official Notion connector in Claude
stays on a personal workspace and is not touched. Every option has to add
workspaces beside it, not instead of it.

## The official connector

Notion's hosted MCP server, added in Claude as the Notion connector. OAuth,
Markdown-native page reading and editing, semantic search, and Notion's own
permission model: it acts as you, seeing what you can see.

It holds one workspace per connection. Signing in again with another workspace
replaces the first.

Kept, for the personal workspace. It is the better experience for the one
workspace it serves, and nothing here competes with it there.

## A custom connector with the same URL

Add `https://mcp.notion.com/mcp` a second time as a custom connector, sign in
to the second workspace through it.

Refused: Claude treats a custom connector with the same URL as an existing one
as a duplicate and does not add it. Dodging the check with a different-looking
URL for the same server would depend on two things nobody promised, Notion's
server accepting the variant and Claude's duplicate check staying literal, so
it was not pursued.

## Switching the connector between workspaces

Disconnect, reconnect, sign in to the other workspace when needed.

Rejected: it takes the personal workspace away for the duration, which is the
one thing that must not happen, and it is too slow to do in the middle of a
task. It also makes "which workspace is connected right now" a piece of state
you have to remember, which is exactly the class of mistake this repo exists to
remove.

## Browser access

Have Claude read Notion pages through a browser that is signed in to the other
workspace, with Claude in Chrome or a similar tool.

Workable for an occasional look, and it needs no setup. Rejected as the main
path: it is slow, it spends a great deal of context on page chrome, and it acts
with your full browser session, so it can edit anything you can, in whichever
workspace the tab happens to be in. There is no read-only mode, no per-workspace
credential, and no check that the right workspace is open.

## Upstream's token passthrough

`@notionhq/notion-mcp-server` can run as one HTTP server that accepts a
different Notion token per connection, in a `Notion-Token` header.

Rejected: it turns the workspace back into something supplied per connection,
by whatever is configuring the client, rather than fixed in a process. It also
means running a long-lived local HTTP server with its own bearer secret, which
is more to secure than a stdio process that dies with the app.

## Multi-workspace servers with a workspace argument

Community servers that hold several Notion tokens and take the workspace as a
parameter on each tool call, some defaulting to all of them when it is omitted.

Rejected for the same reason as their Gmail equivalents: one wrong string reads
or writes the wrong workspace, and a default that fans out to every workspace
is that failure made automatic. One process holding every token also means one
compromise reaches all of them.

## Notion's own multi-workspace features

A single Notion account can belong to several workspaces, but an integration,
and an OAuth connection, is per workspace. Nothing on Notion's side lets one
credential span workspaces, which is good for isolation and is why there is no
shortcut here.

## This design (chosen)

One `bin/notion-mcp <slug>` process per workspace, wrapping Notion's own MCP
server at a pinned version. One internal integration per workspace, created
with Read content only unless writes are needed. The token in the Keychain,
read by slug, never in a config file. An identity check against
`GET /v1/users/me` before the server starts, refusing a mismatch.

Why:

- The workspace cannot be wrong at call time, because it is not a parameter.
- Read-only is enforced by Notion, which the client cannot talk its way past.
- The server code is Notion's, not new code with tokens in it.
- It adds workspaces without touching the official connector.

Known problems, accepted:

- Upstream says the local server is no longer actively maintained and may be
  sunset. The pin protects against surprise changes, not against abandonment.
  If it disappears, the launcher is the reusable part: anything that takes a
  token in its environment can sit behind it.
- Its tools mirror the REST API. Pages come back as block JSON except through
  the one Markdown tool, search is keyword only, and everything costs more
  tokens than the official connector.
- It cannot hide write tools, so read-only workspaces show tools that will
  fail. See [security.md](security.md).
- Every page must be shared with the integration explicitly. The official
  connector sees what you see; this sees what you shared.
- No cross-workspace search.
