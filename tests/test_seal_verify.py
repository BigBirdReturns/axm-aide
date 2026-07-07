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
