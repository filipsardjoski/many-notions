# many-notions

One local Notion MCP server per workspace, next to Claude's official Notion
connector, so you name the workspace instead of passing it as an argument
something could get wrong.

You define the workspaces. With the shipped `workspaces.example.yml` you get:

```
notion-acme           Acme Inc        read
notion-side-project   Side Project    write
```

Two is just the example. One entry in `workspaces.yml` is one server.

Built on Notion's own [`@notionhq/notion-mcp-server`](https://github.com/makenotion/notion-mcp-server),
pinned to an exact version and started once per workspace, each process holding
one workspace's token and nothing else.

## The problem

Claude's official Notion connector holds one workspace. Notion's hosted MCP
server lives at one URL, `https://mcp.notion.com/mcp`, and adding that URL a
second time as a custom connector is refused as a duplicate. Switching the
connector to another workspace takes it away from the first.

Here the official connector stays on the workspace it is on, untouched, and
every other workspace sits beside it as a server of its own.

The obvious alternative, a multi-workspace server that takes the workspace as
a parameter on each tool call, trades one problem for a worse one: a wrong
string reads, or writes, the wrong workspace. See
[docs/alternatives.md](docs/alternatives.md).

## The approach

The workspace is a property of the process, never a tool argument.

```
workspaces.yml                      slug, name, access, one entry per workspace
login Keychain
  service many-notions, account acme            one token per slug
  service many-notions, account side-project

bin/notion-mcp acme
  1. finds 'acme' in workspaces.yml                  unknown slug: exit
  2. reads only the acme Keychain item               missing token: exit
  3. GET /v1/users/me, compares the workspace        mismatch: exit
  4. exec node .../@notionhq/notion-mcp-server/bin/cli.mjs
       env: NOTION_TOKEN=<acme's token>, and an allowlist of nothing risky
```

- **One credential per workspace, and no path from one to another.** A Notion
  internal integration belongs to exactly one workspace, so its token cannot
  reach another. The launcher reads one Keychain item, chosen by the slug on
  its command line, and there is no search, no default and no fallback.
- **Identity is checked before the server exists.** The launcher asks Notion
  which workspace the token belongs to and compares it with the entry: by
  `workspace_id` when the entry pins one and Notion reports one, by name
  otherwise. A mismatch, or an answer that does not say, stops it. A server
  that cannot prove which workspace it serves would be serving whatever the
  token reaches under a name that promises something specific.
- **The child's environment is built, not inherited.** The upstream server
  prefers `OPENAPI_MCP_HEADERS` over `NOTION_TOKEN` and sends everything to
  `BASE_URL` when it is set, so a stray value of either would swap the token
  or the destination after the check had passed. Neither survives, and nor does
  `NODE_OPTIONS`.
- **The name is in every call.** Servers are registered as `notion-<slug>`, so
  a tool appears to Claude as `notion-acme` › `API-post-search`, and the
  workspace is visible at the moment the call is made.
- **The token is never in a file.** Not in `workspaces.yml`, not in
  `claude_desktop_config.json`, not in `~/.claude.json`, not on a command
  line. The configs hold an absolute path and a slug.

The cost is no cross-workspace search: N workspaces are N servers and N
searches. If searching them all at once matters more than isolation, this is
the wrong design.

The other cost is the upstream server itself. Its README now recommends
Notion's hosted server instead and says this one is no longer actively
maintained. Its tools are thin wrappers over the REST API, so they return block
JSON rather than the Markdown the official connector works in, and use more
tokens. It is still the official, MIT-licensed implementation and works with an
integration token, which is the only kind of credential that is scoped to a
workspace and can sit in a Keychain. Pinning an exact version means its state
upstream cannot change what runs here.

## Setup

```bash
git clone <this-repo> ~/Developer/many-notions
cd ~/Developer/many-notions

brew install node          # 20 or later

cp workspaces.example.yml workspaces.yml
$EDITOR workspaces.yml     # your slugs and workspace names. Gitignored.

make install               # npm ci the pinned server, set up the git hooks
```

Then, for each workspace, the Notion side below, followed by:

```bash
make token SLUG=acme       # paste the secret at a hidden prompt
make verify                # identity plus one search, per workspace
```

And once, after all of them:

```bash
make desktop               # register with Claude Desktop
make code                  # register with Claude Code, user scope
make check-mcp             # start each server the way the app will
```

Then quit Claude Desktop completely with `Cmd-Q`, not by closing the window,
and reopen it. It reads its config once at launch.

## The Notion side, once per workspace

1. **Sign in as a workspace owner or admin** of the workspace in question.
   Guests cannot create integrations, and members may not be allowed to,
   depending on the workspace's settings.
2. Open [notion.so/profile/integrations](https://www.notion.so/profile/integrations),
   which Notion now labels **Developer tools › Developer connections**, and
   create a new **internal** connection. Installable in: the workspace whose
   name you wrote in `workspaces.yml`. Its name is what you will pick from the
   Connections menu later, so something like `Claude (read)` is useful.
3. On its **Configuration** tab, under **Capabilities**:
   - `access: read`: tick **Read content** only, and leave Update content and
     Insert content unticked. **Read comments** is safe to keep, since it only
     reads; leave **Insert comments** off. Under user capabilities choose
     **No user information**. Leave the agent capabilities unticked.
   - `access: write`: Read content, Update content and Insert content, plus
     comments if you want them.
   Notion saves each change as you make it.
4. Copy the token from **Integration token › API token** on the same tab, with
   the copy icon at the right-hand end of the field. Older Notion screens call
   it the Internal Integration Secret. Do not paste it into a chat, a file or a
   terminal command. The next step asks for it.
5. Run `make token SLUG=<slug>` and paste the token at the prompt. Nothing is
   echoed, `security` asks for it twice, and it goes straight into the
   Keychain. The script then checks which workspace it belongs to, and deletes
   it again if Notion rejects it or reports a different workspace.
6. **Share the root pages.** A connection sees nothing until pages are shared
   with it. Either add them on its **Content access** tab, or open each
   top-level page's menu (`•••`), choose **Connections**, and add it. Child
   pages are included.
7. Run `make verify`. It fails with `NOTHING SHARED` until step 6 is done.

On first launch from Claude Desktop, macOS can show a Keychain prompt asking
whether `security` may use the `many-notions` item. Choose **Always Allow**.
**Allow** works once and the prompt returns at every launch; **Deny** makes the
launcher exit with a message saying so.

## Read and write

`access: read` is enforced by Notion, not by this repo. An integration created
with only **Read content** gets a 403 from Notion on every write, whatever the
client asks for.

The upstream server cannot hide its write tools. Version 2.6.1 was checked for
it: it has no flag, environment variable or configuration that filters tools,
and it lists all 24 every time, marking the GETs read-only. So a read-only
workspace still shows these to Claude, and Notion refuses each one:

```
API-patch-block-children   API-update-a-block       API-delete-a-block
API-patch-page             API-post-page            API-create-a-comment
API-update-a-data-source   API-create-a-data-source API-move-page
API-update-page-markdown
```

`API-post-search` and `API-query-data-source` are POSTs that upstream also marks
destructive, but they only read, and work with Read content.

The `access` field itself changes nothing at runtime. It records what the
integration was created with, and the launcher and `make verify` print it, so a
mismatch between the two is visible. Notion does not expose an integration's
capabilities to the integration, so nothing here can check them without
attempting a write. What read-only does and does not guarantee is in
[docs/security.md](docs/security.md).

## Commands

| target | does |
| --- | --- |
| `make install` | `npm ci --ignore-scripts` from the lockfile, records the node it used, points git at `hooks/` |
| `make token SLUG=x` | stores x's token in the Keychain from a hidden prompt, then checks its workspace |
| `make verify` | per workspace: token present, workspace matches, one search sees at least one page |
| `make desktop` | merges one `notion-<slug>` entry per workspace into `claude_desktop_config.json` |
| `make code` | `claude mcp add --scope user notion-<slug> -- <repo>/bin/notion-mcp <slug>` per workspace |
| `make check-mcp` | starts each server with the environment Claude Desktop gives it, lists tools, calls `get-self` |
| `make check-leaks` | refuses real workspace names, ids, page URLs, tokens and addresses in publishable files |

`make desktop` backs the config up first, to a timestamped
`claude_desktop_config.json.bak-many-notions-*` next to it, and only adds,
updates or removes entries whose command is this repo's `bin/notion-mcp`.
Every other server in that file, including a hand-made `notion-*` one, is left
as it was; a name collision with one is reported, not resolved. `make code`
follows the same rule in Claude Code's user config.

## Adding another workspace

Add an entry to `workspaces.yml`, do the Notion side for it, then:

```bash
make token SLUG=<slug> && make verify && make desktop && make code
```

Removing an entry and rerunning `make desktop` and `make code` removes its
servers. The Keychain item stays until you delete it:

```bash
security delete-generic-password -s many-notions -a <slug>
```

## Layout

```
workspaces.example.yml   committed template, copy it to workspaces.yml
workspaces.yml           your workspaces, one entry each. Gitignored.
package.json             the exact upstream version
package-lock.json        every transitive dependency, with integrity hashes
bin/notion-mcp           the launcher: slug in, verified server out
lib/workspaces.py        manifest, Keychain, identity check; shared by all
scripts/install.sh       npm ci, node path, git hooks
scripts/token.sh         hidden-prompt Keychain store plus identity check
scripts/verify.py        identity and one search per workspace
scripts/gen-desktop.py   merges entries into Claude Desktop's config
scripts/gen-code.py      registers entries with Claude Code
scripts/check-mcp.py     drives each server the way the desktop app will
scripts/check-leaks.py   refuses private data before it is committed
hooks/                   pre-commit and commit-msg, both run check-leaks
docs/                    security, and why not the other options
```

## What is yours and what is shared

Anything naming a real workspace stays out of the repo.

| Committed | Yours, never committed | How it appears |
| --- | --- | --- |
| `workspaces.example.yml` | `workspaces.yml` | `cp workspaces.example.yml workspaces.yml` |
| `package-lock.json` | `node_modules/`, `.node-path` | `make install` |
| — | Keychain items, service `many-notions` | `make token SLUG=<slug>` |
| — | `notion-*` entries in Claude's configs | `make desktop`, `make code` |
| — | `.leak-words` | your own list for `check-leaks` |

A fresh clone has none of the right-hand column, and that is the point: cloning
this gets you the machinery, never anyone's workspaces or tokens.

`check-leaks` runs on every commit through `hooks/pre-commit`, against the
staged tree, and on every message through `hooks/commit-msg`. It refuses the
slugs, names and ids in `workspaces.yml`, your login name (which is what an
absolute path out of a generated config carries), anything token-shaped,
Notion ids and page URLs, and email addresses outside reserved example domains.
Words the manifest does not know, such as a workspace you have not added yet,
go one per line in `.leak-words`, which is gitignored.

## When it does not work

Claude Desktop logs each server separately:

    ~/Library/Logs/Claude/mcp-server-notion-<slug>.log

The launcher writes one line there on success and the reason on failure.
`make check-mcp` reproduces what the app does and is faster than restarting it.

| Symptom | Cause |
| --- | --- |
| Server missing from the app | config not reloaded. `Cmd-Q` and reopen, not just close the window |
| `no token in the Keychain` | `make token SLUG=<slug>` |
| `the Keychain refused` | Deny was chosen at the prompt. Start again and choose Always Allow |
| `WRONG WORKSPACE` | the token is from another workspace's integration, or `name` is misspelled. Compare with `make verify` |
| `Notion rejected the token` | the secret was regenerated or the integration deleted. New secret, then `make token` |
| `NOTHING SHARED` in verify | no page is shared with the integration yet. Page menu, Connections |
| `node ... is gone` | node moved or was uninstalled. `make install` |
| A write fails with 403 `restricted_resource` | working as intended on a read-only integration |
| A page Claude cannot find | not shared with that integration, or it lives in another workspace |

## Updating the upstream server

```bash
npm install --ignore-scripts --save-exact @notionhq/notion-mcp-server@<version>
make install && make check-mcp
```

Read the upstream changes first; the tool list is generated from its OpenAPI
file and has changed shape between major versions. Then restart Claude
Desktop, which keeps running the old process until it does.

Nothing here uses `npx`. `npx -y @notionhq/notion-mcp-server`, which the
upstream README suggests, resolves the latest published version every time a
server starts, so whatever was published last runs with your token the next
time you open the app. Here the version only changes when you change it.

## License

MIT
