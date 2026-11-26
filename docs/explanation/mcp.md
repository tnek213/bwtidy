# What an agent can do

Besides its web UI, bwtidy is an MCP server. An AI agent such as Claude Code
or Codex can look through your vault and suggest how to tidy it. The agent
never writes a change of its own and never sees a secret value. It only
proposes changes and you decide which ones are written.
[Connect an MCP client](../how-to/mcp.md) shows how to connect one. Every
tool it can call is in [MCP tools](../reference/mcp-tools.md).

```mermaid alt="An agent with the token reaches bwtidy's MCP server while MCP is switched on. Its read tools see the vault without the secret values and the bwtidy note. Its propose tools put each proposal on the Queue page, where you approve it to have it written with a backup first or reject it. A proposal that would copy a secret value into a plain one is rejected at once."
flowchart TD
  Agent(["AI agent<br/>with the token"])
  subgraph Server["bwtidy MCP server while MCP is on"]
    direction LR
    Read["Read tools"]
    Propose["Propose tools"]
  end
  View[("Your vault<br/>without secret values<br/>or the bwtidy note")]
  Queue["Pending on<br/>the Queue page"]
  Write["Written like any edit<br/>with a backup first"]
  Rejected["Rejected"]
  Agent --> Server
  Read --> View
  Propose --> Queue
  Propose -- "copies a<br/>secret value" --> Rejected
  Queue -- "you approve" --> Write
  Queue -- "you reject" --> Rejected
```

## What it can see

The agent sees every item with its plain values, such as these:

- the name, the type and the notes
- the folder, the organization and the collections
- a login's username and its URIs with their match setting
- the custom fields that are not hidden
- the file name and size of each attachment

It also sees which secret fields an item has set but never their values.
[Which values are secret](secrets.md#which-values-are-secret) names those
fields. Beyond the items it sees all of this:

- the folders and the collections
- the templates and the datatypes
- how well each item fits its template
- the custom fields whose names are near duplicates
- its own proposals and what became of them

It never sees the [bwtidy note](tool-note.md) or what an attachment holds.
The backups and the change log are out of its reach too.

## What it can propose

The agent can propose all of these changes:

- move items into or out of a folder
- move items into an organization or back to the personal vault
- set the collections of items
- rename or convert items
- merge or trash items
- rename or merge fields
- apply a template
- rename folders or delete empty ones
- set URIs and their match setting
- rename an attachment
- create an item

A new item's secret fields stay blank for you to fill in. A password or a
hidden field can instead get a generated value that nobody sees until you
reveal it in the grid.

bwtidy checks each proposal against the vault as it arrives. The agent gets an
error for one that does not fit.

Some proposals would copy a secret value into a plain one, such as a guessed
password proposed as a new name. bwtidy refuses them without telling the
agent, so it cannot use refusals to confirm a guess. You see such a proposal
under Rejected right away. The agent sees it as pending until bwtidy decides
on another entry in the queue. From then on it shows as rejected.

The agent can also end your session or log the Bitwarden CLI out. Like a
logout in the web UI this first writes the edits you have waiting. Its
instructions tell it to do either only when you ask.

## You approve every change

A proposal waits on the Queue page with its summary and a diff of what it
changes. The diff masks the secret values. When the agent gave a reason, the
proposal shows it too.

The auto-commit never takes a proposal. You approve or reject them one at a
time or several at once. An approved batch is written like any edit, with a
backup first and a change log row for each change.
[How edits are saved](edits.md) explains that write.

![The Queue page with an agent's pending proposal to rename an item, its checkbox and the Approve selected button outlined](../images/explanation-queue-proposal.png)

## The token and the switch

- The token picks the account. An agent reaches only the account whose token
  it holds. bwtidy keeps only a hash of the token, so it shows the token just
  once.
- MCP works only while it is switched on in the app bar and while you are
  logged in with a tab open. Every login starts with MCP switched off.
- The server answers only requests addressed to localhost or to a host name
  you allowed in `BWTIDY_MCP_HOSTS`. That way a web page cannot reach it
  through a host name of its own.

## For developers

The read tools answer from a view of the vault that hides the bwtidy note and
the secret values. Every propose tool checks its proposal and then puts it in
the same queue as the edits you make in the UI.
[How a write flows](../architecture.md#how-a-write-flows) in
docs/architecture.md follows an edit through the code. Its list of the other
ways in shows where an MCP proposal enters.
