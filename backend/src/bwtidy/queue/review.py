"""The review queue (docs/spec.md, MCP and the queue): every write waits here first.

An entry holds one operation of the write path (the models are in operations.py). enqueue
checks it against the vault in RAM right away, so the caller hears about a malformed or stale
operation at once (intake.py). Approving a batch applies its operations as one call of the write
path (commit.py).

Agent proposals (source mcp) wait for the user's approval. UI edits (source ui) are queued too and
committed by AutoCommit (autocommit.py) without a decision, so the grid never waits for a
sync, a backup and a write.

ReviewQueue is what api/, mcp/ and the auto-commit call. It holds the state (state.py) and hands
each call to the module of its concern: intake.py, commit.py, entries.py (the rows and the
decisions without a write), agent.py (what the agent reads), sections.py (a section as the saves
under way leave it) and wipe.py (the account's data).
"""

from typing import Any

from bwtidy.operations import Operation
from bwtidy.queue import agent, commit, entries, intake, sections, wipe
from bwtidy.queue.state import MCP, QueueState
from bwtidy.sections import Section, SectionChange


class ReviewQueue(QueueState):
    """The queue in SQLite, the vault in RAM and the decrypted backups to check and diff
    against, and the write path."""

    def enqueue(self, op: Operation, summary: str, source: str = MCP) -> int:
        """Check an agent's operation and store it as a pending entry (intake.enqueue)."""
        return intake.enqueue(self, op, summary, source)

    def enqueue_ui(self, ops: list[Operation]) -> list[int]:
        """Queue the UI's operations, one entry each in order (intake.enqueue_ui)."""
        return intake.enqueue_ui(self, ops)

    def enqueue_section_changes(
        self, section: Section, changes: list[SectionChange], summary: str
    ) -> int:
        """Queue changes of one section of the tool note (intake.enqueue_section_changes)."""
        return intake.enqueue_section_changes(self, section, changes, summary)

    def pending_ui_ids(self) -> list[int]:
        return entries.pending_ui_ids(self)

    def pending_mcp_ids(self) -> list[int]:
        return agent.pending_mcp_ids(self)

    def proposals(self, status: str | None = None) -> list[dict[str, Any]]:
        """The agent's entries as it sees them (agent.proposals)."""
        return agent.proposals(self, status)

    def saving_section(self, section: Section) -> tuple[bool, Any]:
        """Whether a save of the section is under way, and the section as the saves leave it
        (sections.saving_section)."""
        return sections.saving_section(self, section)

    def entries(self, status: str | None = None) -> list[dict[str, Any]]:
        """The entries in id order, all or those with the status (entries.entries)."""
        return entries.entries(self, status)

    async def reject(self, ids: list[int]) -> dict[str, Any]:
        return await entries.reject(self, ids)

    async def dismiss(self, ids: list[int]) -> dict[str, Any]:
        """Delete the failed entries among the ids (entries.dismiss)."""
        return await entries.dismiss(self, ids)

    async def expire_failed(self) -> list[int]:
        """Delete the failed entries older than failed_keep_hours (entries.expire_failed)."""
        return await entries.expire_failed(self)

    async def run_failed_expiry(self) -> None:
        """Delete the failed entries that are too old, now and then every EXPIRY_INTERVAL."""
        await entries.run_failed_expiry(self)

    def fail_interrupted(self) -> None:
        """Fail what a killed process left approved (entries.fail_interrupted)."""
        entries.fail_interrupted(self)

    def forget_failed_values(self) -> None:
        """Delete the hidden values of failed entries (entries.forget_failed_values)."""
        entries.forget_failed_values(self)

    async def approve(self, ids: list[int]) -> dict[str, Any]:
        """Apply the pending entries as one operation of the write path (commit.approve)."""
        return await commit.approve(self, ids)

    async def retry(self, ids: list[int], overwrite: bool = False) -> dict[str, Any]:
        """Put failed entries back to pending and approve them at once (commit.retry)."""
        return await commit.retry(self, ids, overwrite)

    async def wipe_account_data(self, *, keep_pending_mcp: bool) -> dict[str, Any]:
        """Delete the account's backups, change log, backup fingerprints and queue entries
        (wipe.wipe_account_data)."""
        return await wipe.wipe_account_data(self, keep_pending_mcp=keep_pending_mcp)
