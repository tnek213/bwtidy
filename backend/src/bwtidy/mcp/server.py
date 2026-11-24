"""The MCP server (docs/spec.md, MCP and the queue): the agent reads the vault and proposes.

It runs inside the backend process, which holds the unlocked session, and is served as
streamable HTTP at /mcp. Every tool answers only while a session is unlocked and a logged in
user has enabled MCP, else it returns DISABLED and nothing else. Besides reading and proposing,
the agent can end the session (end_session) and log the Bitwarden CLI out with it
(log_out_of_bitwarden): each answers first and does it right after the answer went out.

What the agent sees: non-secret values, the names of the secret columns that are set (never
their values), template matches and fill scores, and the datatypes with how well non-secret
values match their format. A secret column's format is never looked at for it, so no pattern is
an oracle on a secret. The tool note does not exist for it. The propose tools never write: each
adds one entry to the review queue, which the user decides in the web UI, and list_proposals shows
the agent what became of its entries. No answer or error carries a secret value, an unexpected
error is reported by its type name only.
"""

import asyncio
import functools
import inspect
import logging
from collections.abc import Awaitable, Callable, Iterable
from types import FunctionType
from typing import Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, InputRequiredResult, TextContent, ToolAnnotations

from bwtidy.bw import BwLogin
from bwtidy.mcp import propose_tools, read_tools, session_tools, texts
from bwtidy.mcp.session_tools import SessionActions
from bwtidy.mcp.vault import AgentVault
from bwtidy.queue.review import ReviewQueue
from bwtidy.session import SessionManager
from bwtidy.vault import Formats

log = logging.getLogger(__name__)

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
PROPOSE = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)
END = ToolAnnotations(
    read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False
)

## how long an ending tool waits after its answer before the session ends, so the answer goes
## out before a worker starts its exit
ANSWER_GRACE = 0.5

type Tool = FunctionType

## what a tool takes first: the agent's view of the vault, or for the session tools their actions
type Interface = type[AgentVault] | type[SessionActions]

## every tool in the order the server lists it, which docs/reference/mcp-tools.md follows
TOOLS: list[tuple[Tool, str, ToolAnnotations, Interface]] = [
    (read_tools.list_items, texts.LIST_ITEMS, READ_ONLY, AgentVault),
    (read_tools.get_item, texts.GET_ITEM, READ_ONLY, AgentVault),
    (read_tools.list_folders, texts.LIST_FOLDERS, READ_ONLY, AgentVault),
    (read_tools.list_collections, texts.LIST_COLLECTIONS, READ_ONLY, AgentVault),
    (read_tools.list_templates, texts.LIST_TEMPLATES, READ_ONLY, AgentVault),
    (read_tools.list_datatypes, texts.LIST_DATATYPES, READ_ONLY, AgentVault),
    (read_tools.list_near_duplicates, texts.LIST_NEAR_DUPLICATES, READ_ONLY, AgentVault),
    (read_tools.list_proposals, texts.LIST_PROPOSALS, READ_ONLY, AgentVault),
    (propose_tools.propose_move_to_folder, texts.PROPOSE_MOVE_TO_FOLDER, PROPOSE, AgentVault),
    (propose_tools.propose_rename_folder, texts.PROPOSE_RENAME_FOLDER, PROPOSE, AgentVault),
    (propose_tools.propose_delete_folder, texts.PROPOSE_DELETE_FOLDER, PROPOSE, AgentVault),
    (propose_tools.propose_set_collections, texts.PROPOSE_SET_COLLECTIONS, PROPOSE, AgentVault),
    (
        propose_tools.propose_move_to_organization,
        texts.PROPOSE_MOVE_TO_ORGANIZATION,
        PROPOSE,
        AgentVault,
    ),
    (propose_tools.propose_move_to_personal, texts.PROPOSE_MOVE_TO_PERSONAL, PROPOSE, AgentVault),
    (propose_tools.propose_rename_item, texts.PROPOSE_RENAME_ITEM, PROPOSE, AgentVault),
    (propose_tools.propose_set_uris, texts.PROPOSE_SET_URIS, PROPOSE, AgentVault),
    (propose_tools.propose_set_uri_match, texts.PROPOSE_SET_URI_MATCH, PROPOSE, AgentVault),
    (propose_tools.propose_rename_field, texts.PROPOSE_RENAME_FIELD, PROPOSE, AgentVault),
    (propose_tools.propose_merge_columns, texts.PROPOSE_MERGE_COLUMNS, PROPOSE, AgentVault),
    (propose_tools.propose_apply_template, texts.PROPOSE_APPLY_TEMPLATE, PROPOSE, AgentVault),
    (propose_tools.propose_convert_item, texts.PROPOSE_CONVERT_ITEM, PROPOSE, AgentVault),
    (propose_tools.propose_merge_items, texts.PROPOSE_MERGE_ITEMS, PROPOSE, AgentVault),
    (propose_tools.propose_rename_attachment, texts.PROPOSE_RENAME_ATTACHMENT, PROPOSE, AgentVault),
    (propose_tools.propose_trash_item, texts.PROPOSE_TRASH_ITEM, PROPOSE, AgentVault),
    (propose_tools.propose_create_item, texts.PROPOSE_CREATE_ITEM, PROPOSE, AgentVault),
    (session_tools.end_session, texts.END_SESSION, END, SessionActions),
    (session_tools.log_out_of_bitwarden, texts.LOG_OUT_OF_BITWARDEN, END, SessionActions),
]


def _safe(fn: Tool, interface: AgentVault | SessionActions) -> Callable[..., Awaitable[str]]:
    """The tool with its interface bound as its first argument, an unexpected error reported by
    its type only: its text could carry a value."""

    @functools.wraps(fn)
    async def run(**arguments: Any) -> str:
        try:
            return await fn(interface, **arguments)
        except ToolError:
            raise
        except Exception as error:
            log.warning("MCP tool %s failed: %s", fn.__name__, type(error).__name__)
            raise ToolError(f"bwtidy failed with {type(error).__name__}") from None

    ## the SDK builds the tool's parameters from this signature, which leaves the interface out
    signature = inspect.signature(fn)
    vars(run)["__signature__"] = signature.replace(
        parameters=list(signature.parameters.values())[1:]
    )
    return run


class BwtidyMcp(MCPServer):
    """MCPServer that answers DISABLED for every tool call while MCP is off or locked.

    The check runs before the arguments are validated, so a disabled server answers nothing
    else, not even an argument error.
    """

    def __init__(self, agent: AgentVault) -> None:
        super().__init__("bwtidy", instructions=texts.INSTRUCTIONS)
        self.agent = agent
        self._after_answer: set[asyncio.Task[None]] = set()

    def after_answer(self, work: Callable[[], Awaitable[None]]) -> None:
        """Run work once the tool's answer had ANSWER_GRACE seconds to go out."""

        async def run() -> None:
            await asyncio.sleep(ANSWER_GRACE)
            try:
                await work()
            except Exception as error:
                log.warning("MCP: the work after the answer failed: %s", type(error).__name__)

        task = asyncio.create_task(run())
        self._after_answer.add(task)
        task.add_done_callback(self._after_answer.discard)

    async def settle(self) -> None:
        """Wait for the work after the answers, before the app shuts down.

        A task that is done can still be in the set until its done callback ran, and a gather of
        done tasks answers without giving the loop a turn, so only the running ones are waited for.
        """
        while running := [task for task in self._after_answer if not task.done()]:
            await asyncio.gather(*running, return_exceptions=True)

    async def call_tool(
        self, name: str, arguments: dict[str, Any], context: Context[Any, Any] | None = None
    ) -> CallToolResult | InputRequiredResult:
        if not self.agent.enabled():
            return CallToolResult(content=[TextContent(type="text", text=texts.DISABLED)])
        return await super().call_tool(name, arguments, context)


def create_mcp_server(
    session: SessionManager, queue: ReviewQueue, bw_login: BwLogin, formats: Formats
) -> BwtidyMcp:
    agent = AgentVault(session, queue, formats)
    server = BwtidyMcp(agent)
    actions = SessionActions(session, bw_login, server.after_answer)
    interfaces: dict[Interface, AgentVault | SessionActions] = {
        AgentVault: agent,
        SessionActions: actions,
    }
    for fn, description, annotations, interface in TOOLS:
        server.add_tool(
            _safe(fn, interfaces[interface]),
            name=fn.__name__,
            description=description,
            annotations=annotations,
            structured_output=False,
        )
    return server


def streamable_http_security(hosts: Iterable[str] = ()) -> TransportSecuritySettings:
    """Only requests addressed to the loopback host or one of `hosts` (BWTIDY_MCP_HOSTS).

    A web page cannot rebind a name of its own to bwtidy then.
    """
    allowed_hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    allowed_origins = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]
    for name in hosts:
        allowed_hosts += [name, f"{name}:*"]
        allowed_origins.append(f"https://{name}")
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )
