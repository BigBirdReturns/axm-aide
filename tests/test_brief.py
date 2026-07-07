"""Kernel + DuckDB gated: the morning read.

Seals a journal, two tasks (one marked done via a second shard), and a session
with a proposal; runs the brief; asserts the open task is shown, the done task
is NOT, and the proposal is listed pending. Needs the real kernel (to seal +
verify) and axm-core's SpectraEngine (to mount + query). Skips cleanly if
either is absent.
"""
from __future__ import annotations

import pytest


def _require_kernel():
    try:
        from axm_build.sign import hybrid1_keygen
    except ImportError as exc:  # pragma: no cover
        pytest.skip(f"genesis kernel not importable: {exc}")
    try:
        hybrid1_keygen()
    except NotImplementedError:  # pragma: no cover
        pytest.skip("genesis kernel stub active (no real signing backend)")


def _require_core():
    try:
        import axiom_runtime.engine  # noqa: F401
        import duckdb  # noqa: F401
    except ImportError as exc:  # pragma: no cover
        pytest.skip(f"axm-core / duckdb not importable: {exc}")


def test_brief_shows_open_hides_done_lists_proposal(tmp_path, monkeypatch):
    _require_kernel()
    _require_core()
    monkeypatch.setenv("SPECTRA_DEV_MODE", "1")

    from axm_aide.records import (
        seal_journal, seal_task_add, seal_task_status, seal_session,
    )
    from axm_aide.brief import build_brief

    shards, keys = tmp_path / "shards", tmp_path / "keys"

    seal_journal("recorded a thought", tags=["insight"], shard_dir_root=shards, key_dir=keys,
                 created_at="2026-07-07T08:00:00Z")
    open_task = seal_task_add("Stay open", due="2026-07-20", shard_dir_root=shards, key_dir=keys,
                              created_at="2026-07-07T08:01:00Z")
    done_task = seal_task_add("Get done", shard_dir_root=shards, key_dir=keys,
                              created_at="2026-07-07T08:02:00Z")
    seal_task_status(done_task.record_id, "done", shard_dir_root=shards, key_dir=keys,
                     created_at="2026-07-07T09:00:00Z")
    sess = seal_session(proposals=["Email the report to finance"],
                        shard_dir_root=shards, key_dir=keys,
                        created_at="2026-07-07T09:30:00Z")

    brief = build_brief(shards, keys / "publisher.pub", last_n=5)

    open_ids = {t["task"] for t in brief.open_tasks}
    assert f"task/{open_task.record_id}" in open_ids       # open task shown
    assert f"task/{done_task.record_id}" not in open_ids   # done task hidden

    # The journal entry is listed (by id + tags), never summarized.
    assert any(j["entry"] == "journal/" + brief.journal[0]["entry"].split("/")[1]
               for j in brief.journal)
    assert brief.journal[0]["tags"] == ["insight"]

    # The proposal is pending (no disposition record exists).
    pending_ids = {p["proposal"] for p in brief.pending_proposals}
    assert f"proposal/{sess.record_id}.1" in pending_ids

    # Provenance names every consulted shard (all verified out of band).
    assert len(brief.consulted) == 5
    assert all(sid.startswith("sh1_") for sid in brief.consulted)
    assert not brief.skipped  # nothing failed verification
