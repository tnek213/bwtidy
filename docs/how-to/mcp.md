# Connect an MCP client

bwtidy serves an MCP server (streamable HTTP) at `/mcp` of its address, so an
AI agent can look through your vault without secret values and propose
changes. Nothing is written until you approve the proposals on the Queue
page. What an agent sees and may propose is in [What an agent can
do](../explanation/mcp.md). The tools it can call are listed in
[MCP tools](../reference/mcp-tools.md).

Below, URL stands for the address you open bwtidy at, without a path (for
example https://bwtidy.example, or http://127.0.0.1:8765 on the same host),
and TOKEN for your MCP token. The commands spell the MCP address as `URL/mcp`.
The token dialog's MCP URL field already shows it that way, ready to paste in
place of `URL/mcp`.

## Make a token

1. Log in to bwtidy, open MCP token in the app bar and click Create.
2. Pick how long the token lives: 7, 30, 90 (the default) or 365 days, or No
   expiry. A token without an expiry works until you remove it.
3. The dialog shows the token once. Copy it before you close the dialog:
   bwtidy keeps only a hash of it and cannot show it again.

![The MCP token dialog with the token field, the MCP URL field and the link to this page, which is outlined](../images/mcp-token-dialog.png)

The dialog also gives the MCP URL (`URL/mcp`) to copy, and links to this
page. Put the token and the MCP URL in the command of your client below.

## Claude Code

```sh
claude mcp add -s user --transport http bwtidy URL/mcp --header "Authorization: Bearer TOKEN"
```

Saved in `~/.claude.json` for your user, so it works in every directory.

## Codex

```sh
export BWTIDY_MCP_TOKEN=TOKEN
codex mcp add bwtidy --url URL/mcp --bearer-token-env-var BWTIDY_MCP_TOKEN
```

Saved in `~/.codex/config.toml` for every directory. Codex reads the token
from the environment variable, so set it where Codex starts, for example in
your shell profile.

## GitHub Copilot CLI

```sh
copilot mcp add --transport http --header "Authorization: Bearer TOKEN" bwtidy URL/mcp
```

Saved in `~/.copilot/mcp-config.json` for your user.

## Gemini CLI

```sh
gemini mcp add -s user --transport http --header "Authorization: Bearer TOKEN" bwtidy URL/mcp
```

Saved in `~/.gemini/settings.json` for your user. Without `-s user` it is
saved for the current project only.

## Another client

Send the header `Authorization: Bearer TOKEN` with every request to `URL/mcp`.

## Switch MCP on

Switch MCP on in the app bar. While it is on, the switch is green and the app
bar takes on a red tint. Every login starts with MCP off, so switch it on
again after each login. The switch stays off, and says why, while you have no
token or it has expired.

## Look after the token

The MCP token menu shows when the token was made, when it expires and when it
was last used.

- Replace makes a new token, and the old one stops working at once. Update
  your client with the new one.
- Remove turns the token off.
- A chip in the app bar warns when 3 days or 15% of the token's lifetime are
  left, whichever comes first.

## Behind a reverse proxy

The MCP server only answers requests addressed to 127.0.0.1, localhost or
::1. Behind a reverse proxy, add your host name to `BWTIDY_MCP_HOSTS` in
`.env` (comma separated, like `BWTIDY_MCP_HOSTS=bwtidy.example`) and run the
command of [Start it](run.md#start-it) again. The whole proxy setup is in
[Behind a reverse proxy](run.md#behind-a-reverse-proxy).

## When a request fails

- 401 (bwtidy MCP token expired): make a new token with Replace and update
  your client.
- 401 for any other reason: the token is wrong or was removed.
- 503 (bwtidy is locked for this account): the token picks the account, and
  that account is not logged in. Log in in the web UI.
- Every tool answers that bwtidy MCP is disabled: switch MCP on in the app
  bar. The tools only answer while a logged in tab is open.
