"""
axm_aide — the AXM aide spoke.

A sovereign personal assistant whose memory has custody and whose judgment is
surrendered. It journals, tracks tasks, and records agent work sessions as
genesis-sealed shards (detached-verifiable forever), makes them queryable, and
PROPOSES actions it never executes: a proposal is a sealed record that requires
a HUMAN disposition through the ecosystem's review flow.

Doctrine: never mint a shard_id (genesis derives it); one custody model; no
interpretation without a gate (tags/statuses are caller-supplied, never
inferred); the machine never decides.
"""
__version__ = "0.1.0"

from axm_aide.records import (  # noqa: E402
    SUITE,
    DEFAULT_SHARD_DIR,
    DEFAULT_KEY_DIR,
    EVIDENCE_TIER,
    EVIDENCE_LIMITS,
    DISPOSITION_VOCAB,
    SealResult,
    get_or_create_keypair,
    seal_journal,
    seal_task_add,
    seal_task_status,
    seal_session,
)

# Backward-compatible underscore alias (mirrors the sibling convention).
_get_or_create_keypair = get_or_create_keypair

__all__ = [
    "SUITE", "DEFAULT_SHARD_DIR", "DEFAULT_KEY_DIR",
    "EVIDENCE_TIER", "EVIDENCE_LIMITS", "DISPOSITION_VOCAB",
    "SealResult", "get_or_create_keypair", "_get_or_create_keypair",
    "seal_journal", "seal_task_add", "seal_task_status", "seal_session",
]
