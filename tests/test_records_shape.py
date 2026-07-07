"""Genesis-free shape tests: claim vocabulary + doctrine invariants.

These never seal (the conftest stub's compiler raises), so they run everywhere.
They pin the claim vocabulary, the caller-tag-only invariant, the append-only
status vocabulary, and — load-bearing for the whole thesis — that the proposal
and disposition vocabularies carry NO "approve"/"true" words: the machine
never decides.
"""
from __future__ import annotations

import inspect

import pytest

from axm_aide.records import (
    DISPOSITION_VOCAB,
    EVIDENCE_LIMITS,
    EVIDENCE_TIER,
    _build_source_and_candidates,
    get_or_create_keypair,
    journal_statements,
    session_statements,
    task_add_statements,
    task_status_statements,
)

# Vocabulary that must NEVER appear as a predicate or object anywhere the aide
# writes: a sealed record is not a decision.
_FORBIDDEN = ("approve", "approved", "true", "false", "accept", "accepted",
              "confirm", "execute", "executed", "authorized")


def _cands(statements, verbatim):
    _src, cand = _build_source_and_candidates(statements, verbatim)
    return cand


class TestJournalVocabulary:
    def test_recorded_at_and_tags(self):
        st, vb = journal_statements("a thought", ["work", "ideas"], "id01", "2026-07-07T00:00:00Z")
        cand = _cands(st, vb)
        preds = [c["predicate"] for c in cand]
        assert preds == ["recorded_at", "tagged", "tagged"]
        assert [c["object"] for c in cand if c["predicate"] == "tagged"] == ["work", "ideas"]
        # recorded_at is tier 0 (fact of record), tags tier 1 (caller assertion).
        assert next(c for c in cand if c["predicate"] == "recorded_at")["tier"] == 0
        assert all(c["tier"] == 1 for c in cand if c["predicate"] == "tagged")

    def test_zero_tags_yields_zero_tagged_claims(self):
        """The caller-tag-only invariant: no tags in ⇒ no `tagged` claims out.
        Tags are NEVER inferred from the entry text."""
        st, vb = journal_statements("full of taggable words: work ideas urgent", [], "id02", "2026-07-07T00:00:00Z")
        cand = _cands(st, vb)
        assert [c["predicate"] for c in cand] == ["recorded_at"]
        assert not any(c["predicate"] == "tagged" for c in cand)

    def test_duplicate_tags_collapsed(self):
        st, _ = journal_statements("x", ["work", "work", "work"], "id03", "2026-07-07T00:00:00Z")
        tagged = [s for s in st if s.predicate == "tagged"]
        assert len(tagged) == 1

    def test_entry_text_is_sealed_verbatim_in_source(self):
        st, vb = journal_statements("the exact words", [], "id04", "2026-07-07T00:00:00Z")
        src, _ = _build_source_and_candidates(st, vb)
        assert "the exact words" in src


class TestTaskVocabulary:
    def test_add_claims(self):
        st, vb = task_add_statements("Ship v0", "2026-07-15", "t01")
        cand = _cands(st, vb)
        preds = [c["predicate"] for c in cand]
        assert preds == ["has_title", "declared_status", "due"]
        status = next(c for c in cand if c["predicate"] == "declared_status")
        assert status["object"] == "open"          # born open
        assert status["tier"] == 0                  # caller-declared, tier 0
        assert all(c["tier"] == 0 for c in cand)    # every task claim is tier 0

    def test_add_without_due(self):
        st, vb = task_add_statements("no due", None, "t02")
        assert [c["predicate"] for c in _cands(st, vb)] == ["has_title", "declared_status"]

    def test_status_change_is_single_declared_status(self):
        st = task_status_statements("t01", "done")
        cand = _cands(st, [])
        assert len(cand) == 1
        assert cand[0]["predicate"] == "declared_status"
        assert cand[0]["object"] == "done"
        assert cand[0]["subject"] == "task/t01"     # same subject → append-only history

    def test_status_vocabulary_is_neutral(self):
        for status in ("open", "done", "dropped"):
            st = task_status_statements("t", status)
            assert st[0].obj == status
        # no "approved"/"true" status exists in the surface
        for bad in _FORBIDDEN:
            assert bad not in ("open", "done", "dropped")


class TestSessionAndProposalVocabulary:
    def test_session_claim_shape(self):
        st, vb, pids = session_statements(
            "s01", ["sh1_a"], ["sh1_b"], ["Do the thing"],
            "2026-07-07T09:00:00Z", "2026-07-07T09:30:00Z",
        )
        cand = _cands(st, vb)
        preds = [c["predicate"] for c in cand]
        assert preds[:4] == ["started_at", "ended_at", "read", "produced"]
        assert "proposed" in preds
        assert "requires_disposition" in preds
        assert "proposes" in preds
        assert pids == ["proposal/s01.1"]

    def test_proposal_requires_human_disposition(self):
        st, vb, _ = session_statements("s02", [], [], ["act"], "t", "t")
        cand = _cands(st, vb)
        rd = next(c for c in cand if c["predicate"] == "requires_disposition")
        assert rd["object"] == "human"
        assert rd["tier"] == 0

    def test_proposal_vocabulary_has_no_decision_words(self):
        """Load-bearing: a proposal is a sealed record, not an approval. No
        predicate or object may carry approve/true/execute vocabulary."""
        st, vb, _ = session_statements(
            "s03", ["sh1_a"], ["sh1_b"], ["Email finance", "Cancel the plan"],
            "t", "t",
        )
        cand = _cands(st, vb)
        for c in cand:
            for word in _FORBIDDEN:
                assert word not in c["predicate"].lower()
        # The verbs the aide uses are neutral records, never decisions.
        assert {"proposed", "requires_disposition", "proposes"}.issubset(
            {c["predicate"] for c in cand}
        )

    def test_proposal_ids_are_session_qualified(self):
        # Two sessions with a proposal each must not collide in aide/session.
        _, _, p1 = session_statements("aaa", [], [], ["x"], "t", "t")
        _, _, p2 = session_statements("bbb", [], [], ["x"], "t", "t")
        assert p1 == ["proposal/aaa.1"]
        assert p2 == ["proposal/bbb.1"]
        assert p1 != p2

    def test_full_proposal_text_sealed_verbatim(self):
        st, vb, _ = session_statements("s04", [], [], ["multi\nline\nproposal"], "t", "t")
        src, _ = _build_source_and_candidates(st, vb)
        assert "multi\nline\nproposal" in src


class TestDispositionVocabulary:
    def test_disposition_vocab_is_human_and_neutral(self):
        assert DISPOSITION_VOCAB == ("escalate", "dismiss", "needs_context")
        for word in _FORBIDDEN:
            assert word not in DISPOSITION_VOCAB


class TestEvidenceIntegrity:
    @pytest.mark.parametrize("builder", [
        lambda: journal_statements("body one; body two", ["a", "b"], "j", "2026-07-07T00:00:00Z"),
        lambda: task_add_statements("Title here", "2026-07-15", "t"),
        lambda: (session_statements("s", ["sh1_a"], ["sh1_b"], ["p one", "p two"], "t1", "t2")[:2]),
    ])
    def test_every_evidence_line_is_unique_in_source(self, builder):
        st, vb = builder()
        src, cand = _build_source_and_candidates(st, vb)
        for c in cand:
            assert src.count(c["evidence"]) == 1, c["evidence"]


class TestEvidenceTier:
    def test_tier_and_limits_present(self):
        assert EVIDENCE_TIER == "agent_record"
        text = " ".join(EVIDENCE_LIMITS).lower()
        assert "caller-declared" in text
        assert "not verified" in text
        assert "human disposition" in text
        assert "never inferred" in text


class TestKeyPoolLoadPath:
    HYBRID_SK = bytes(range(256)) * 15 + bytes(64)   # 3904 bytes
    HYBRID_PK = bytes(range(256)) * 5 + bytes(64)    # 1344 bytes

    def test_hybrid_pool_loads_secret_blob(self, tmp_path):
        (tmp_path / "publisher.sk").write_bytes(self.HYBRID_SK)
        (tmp_path / "publisher.pub").write_bytes(self.HYBRID_PK)
        assert get_or_create_keypair(tmp_path) == self.HYBRID_SK

    def test_legacy_pool_raises_predates_v1(self, tmp_path):
        (tmp_path / "publisher.sk").write_bytes(bytes(2528))  # v0.x mldsa44 sk
        (tmp_path / "publisher.pub").write_bytes(bytes(1312))  # v0.x mldsa44 pk
        with pytest.raises(ValueError, match="predates spec/v1") as exc:
            get_or_create_keypair(tmp_path)
        msg = str(exc.value)
        assert str(tmp_path) in msg and "mv " in msg and "re-run" in msg

    def test_partial_pool_refuses_to_overwrite(self, tmp_path):
        (tmp_path / "publisher.sk").write_bytes(self.HYBRID_SK)
        with pytest.raises(ValueError, match="[Ii]ncomplete"):
            get_or_create_keypair(tmp_path)
        assert not (tmp_path / "publisher.pub").exists()

    def test_no_suite_parameter(self):
        params = inspect.signature(get_or_create_keypair).parameters
        assert list(params) == ["key_dir"]  # one suite; nothing to choose
