"""Kernel-gated end-to-end: seal each kind, detached-verify against an
out-of-band key, and prove the append-only status history.

These need the real genesis kernel with its ML-DSA backend (the conftest stub
raises on signing). They skip cleanly when it is absent. HOME is redirected via
explicit shard_dir_root / key_dir so ~/.axm is never touched.
"""
from __future__ import annotations

from pathlib import Path

import pytest


def _kernel():
    """Return the real verifier, or skip if the kernel/backend is absent."""
    try:
        from axm_build.sign import hybrid1_keygen
        from axm_verify.logic import verify_shard
    except ImportError as exc:  # pragma: no cover - env-dependent
        pytest.skip(f"genesis kernel not importable: {exc}")
    try:
        hybrid1_keygen()  # smoke: real signing backend present?
    except NotImplementedError:  # pragma: no cover - conftest stub active
        pytest.skip("genesis kernel stub active (no real signing backend)")
    return verify_shard


@pytest.fixture
def pool(tmp_path):
    """A shard dir + key dir under tmp_path (never ~/.axm)."""
    return tmp_path / "shards", tmp_path / "keys"


def test_journal_seals_and_verifies(pool):
    verify_shard = _kernel()
    from axm_aide.records import seal_journal

    shards, keys = pool
    res = seal_journal("a sealed thought", tags=["work"], shard_dir_root=shards, key_dir=keys)
    assert res.shard_id.startswith("sh1_")
    assert verify_shard(res.shard_dir, trusted_key_path=keys / "publisher.pub")["status"] == "PASS"


def test_task_seals_and_verifies(pool):
    verify_shard = _kernel()
    from axm_aide.records import seal_task_add

    shards, keys = pool
    res = seal_task_add("Ship v0", due="2026-07-15", shard_dir_root=shards, key_dir=keys)
    assert verify_shard(res.shard_dir, trusted_key_path=keys / "publisher.pub")["status"] == "PASS"


def test_session_with_proposal_seals_and_verifies(pool):
    verify_shard = _kernel()
    from axm_aide.records import seal_session

    shards, keys = pool
    res = seal_session(
        reads=["sh1_" + "a" * 64], produces=["sh1_" + "b" * 64],
        proposals=["Email the report to finance"],
        shard_dir_root=shards, key_dir=keys,
    )
    assert res.extra["proposals"] == [f"proposal/{res.record_id}.1"]
    assert verify_shard(res.shard_dir, trusted_key_path=keys / "publisher.pub")["status"] == "PASS"


def test_wrong_key_fails(pool, tmp_path):
    verify_shard = _kernel()
    from axm_build.sign import hybrid1_keygen
    from axm_aide.records import seal_journal

    shards, keys = pool
    res = seal_journal("signed by the pool", shard_dir_root=shards, key_dir=keys)

    # A DIFFERENT, out-of-band governance key must reject the shard.
    foreign_pub, _foreign_sec = hybrid1_keygen()
    foreign = tmp_path / "governance.pub"
    foreign.write_bytes(foreign_pub)

    result = verify_shard(res.shard_dir, trusted_key_path=foreign)
    assert result["status"] == "FAIL"
    assert any(e["code"] == "E_SIG_INVALID" for e in result["errors"]), result["errors"]


def test_status_history_is_append_only(pool):
    """A status change is a NEW shard; the original open shard is never
    rewritten. Both shards verify, and each retains its own declared_status."""
    verify_shard = _kernel()
    from axm_aide.records import seal_task_add, seal_task_status

    shards, keys = pool
    add = seal_task_add("A task", shard_dir_root=shards, key_dir=keys,
                        created_at="2026-07-07T09:00:00Z")
    done = seal_task_status(add.record_id, "done", shard_dir_root=shards, key_dir=keys,
                            created_at="2026-07-07T10:00:00Z")

    # Two distinct shard directories exist for the same task id.
    assert add.shard_dir != done.shard_dir
    assert add.shard_dir.exists() and done.shard_dir.exists()

    tk = keys / "publisher.pub"
    assert verify_shard(add.shard_dir, trusted_key_path=tk)["status"] == "PASS"
    assert verify_shard(done.shard_dir, trusted_key_path=tk)["status"] == "PASS"

    # The original shard still declares "open" (unchanged); the new one "done".
    add_src = (add.shard_dir / "content" / "source.txt").read_text()
    done_src = (done.shard_dir / "content" / "source.txt").read_text()
    assert 'declared_status "open"' in add_src
    assert 'declared_status "done"' in done_src
    assert 'declared_status "done"' not in add_src   # original never rewritten


def test_same_second_status_ties_break_by_sequence_not_name(pool):
    """Control question (adversarial-review finding 1): two status changes in
    the SAME second must resolve to the one sealed LAST — never to whichever
    status word sorts later in the shard-name listing."""
    _kernel()
    from axm_aide.cli import _read_jsonl  # the kernel-only resolution path
    from axm_aide.records import seal_task_add, seal_task_status

    shards, keys = pool
    ts = "2026-07-07T00:00:00Z"
    t = seal_task_add("tie break me", shard_dir_root=shards, key_dir=keys, created_at=ts)
    # Same second, sealed in this order: open -> dropped. Alphabetical shard
    # order would resurrect "open"; the sequence must keep "dropped".
    seal_task_status(t.record_id, "open", shard_dir_root=shards, key_dir=keys, created_at=ts)
    seal_task_status(t.record_id, "dropped", shard_dir_root=shards, key_dir=keys, created_at=ts)

    # Resolve exactly as `task list` does: created_at + status_seq per shard.
    current = {}
    for sp in sorted(shards.glob("aide_task_*")):
        import json
        m = json.loads((sp / "manifest.json").read_text())
        created = str((m.get("metadata") or {}).get("created_at", ""))
        claims = list(_read_jsonl(sp / "graph" / "claims.jsonl"))
        seq = next((int(c["object"]) for c in claims if c["predicate"] == "status_seq"), 0)
        for c in claims:
            if c["predicate"] == "declared_status":
                key = (created, seq)
                if key >= current.get("key", ("", -1)):
                    current = {"key": key, "status": c["object"]}
    assert current["status"] == "dropped"


def test_same_second_same_status_shards_do_not_overwrite(pool):
    """Two identical statuses in the same second are two events on disk —
    the sequence in the shard name keeps the append-only store append-only."""
    _kernel()
    from axm_aide.records import seal_task_add, seal_task_status

    shards, keys = pool
    ts = "2026-07-07T00:00:00Z"
    t = seal_task_add("twice", shard_dir_root=shards, key_dir=keys, created_at=ts)
    a = seal_task_status(t.record_id, "done", shard_dir_root=shards, key_dir=keys, created_at=ts)
    b = seal_task_status(t.record_id, "done", shard_dir_root=shards, key_dir=keys, created_at=ts)
    assert a.shard_dir != b.shard_dir


def test_colliding_caller_content_refuses_cleanly(pool):
    """Control question (adversarial-review finding 2): caller text that
    reproduces one of the record's own claim lines must refuse with a clear
    aide-level error, not a raw kernel traceback."""
    _kernel()
    from axm_aide.records import seal_journal

    shards, keys = pool
    ts = "2026-07-07T00:00:00Z"
    eid = "cafe0001"
    hostile = f'journal/{eid} recorded_at "{ts}"'   # duplicates the header claim line
    with pytest.raises(ValueError, match="reproduces one of its own claim lines"):
        seal_journal(hostile, shard_dir_root=shards, key_dir=keys,
                     created_at=ts, entry_id=eid)
