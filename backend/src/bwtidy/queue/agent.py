"""What the agent reads of its own entries (list_proposals).

A silent refusal (intake.py) shows pending until the next decision on another entry, then
rejected, with the summary it was answered, kept as an age ciphertext.
"""

import json
from typing import Any

from bwtidy.queue.state import FAILED, MCP, PENDING, REJECTED, QueueState
from bwtidy.vault import HiddenValueError

## the one word the agent gets for why an entry failed, never its detail: that could hold a value
FAILURE_REASONS = {
    "conflicts": "conflict",
    "mergeConflicts": "merge conflict",
    "writeFailed": "write failed",
    "interrupted": "interrupted",
    "invalid": "invalid",
}
## what the agent reads of an entry whose summary cannot be decrypted (the identity was replaced)
UNREADABLE_SUMMARY = "(the summary cannot be read)"
## the newest entries list_proposals answers at most
PROPOSALS_LIMIT = 100


def proposals(q: QueueState, status: str | None = None) -> list[dict[str, Any]]:
    """The agent's entries as it sees them, newest first, at most PROPOSALS_LIMIT.

    A silent refusal reads as pending until a decision on another entry came after it, then
    as rejected at that decision's time (agent_decided_at), so its answer is that of an entry
    the user has not decided yet or rejected. Every listed summary is decrypted, the refused and
    the others alike. A failed entry's reason is one fixed word, never its detail.
    """
    shown = []
    for row in reversed(q.db.queue_entries(source=MCP)):
        seen, decided_at = row["status"], row["decided_at"]
        if row["refused_secret"]:
            decided_at = row["agent_decided_at"]
            seen = PENDING if decided_at is None else REJECTED
        if status is not None and seen != status:
            continue
        reason = None
        if seen == FAILED:
            result = json.loads(row["result"]) if row["result"] else {}
            reason = FAILURE_REASONS.get(result.get("error"), "failed")
        shown.append(
            {
                "id": row["id"],
                "summary": row["agent_summary"],
                "status": seen,
                "createdAt": row["created_at"],
                "decidedAt": decided_at,
                "reason": reason,
            }
        )
        if len(shown) == PROPOSALS_LIMIT:
            break
    for entry in shown:
        entry["summary"] = _agent_summary(q, entry["summary"])
    return shown


def _agent_summary(q: QueueState, ciphertext: str | None) -> str:
    if ciphertext is None:
        return UNREADABLE_SUMMARY
    try:
        return q.hidden.decrypt(ciphertext)
    except HiddenValueError:
        return UNREADABLE_SUMMARY


def pending_mcp_ids(q: QueueState) -> list[int]:
    """The agent's proposals still waiting for a decision (not the silent refusals, which
    are stored already rejected)."""
    return [row["id"] for row in q.db.queue_entries(statuses=(PENDING,), source=MCP)]
