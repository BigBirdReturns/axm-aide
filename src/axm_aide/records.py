"""
axm_aide.records — the three record kinds, each sealed as its own genesis shard.

The aide keeps custody of memory and surrenders judgment. Everything it writes
is a CALLER-DECLARED statement, sealed through the genesis kernel so it is
detached-verifiable forever. Three kinds:

  * Journal  (namespace ``aide/journal``) — an entry, verbatim, plus caller tags.
  * Task     (namespace ``aide/task``)    — a title + a caller-declared status.
  * Session  (namespace ``aide/session``) — what an agent work session did, and
                                            what it PROPOSES (never executes).

Doctrine enforced here:
  * The aide never mints a shard_id — genesis derives it (``derive_shard_id``).
  * One custody model — every kind goes through ``compile_generic_shard``.
  * No interpretation without a gate — tags and statuses are caller-supplied,
    never inferred; the aide summarizes nothing into a claim.
  * The machine never decides — a proposal is a sealed record that
    ``requires_disposition "human"``. There is no approve/true vocabulary here;
    disposition happens downstream, in a human review flow.

Compilation, signing, Merkle construction and identity all belong to the
kernel (``axm_build`` / ``axm_verify``). This module only translates a
caller's input into candidates + canonical source text and invokes the
compiler — exactly the documented spoke privilege (see axm-core/SPOKE_API.md).
"""
from __future__ import annotations

import json
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

# Direct imports — declared dependencies. If these fail, the install is broken.
# The aide reimplements none of this; it drives the one kernel every shard in
# the ecosystem is compiled by.
from axm_build.compiler_generic import CompilerConfig, compile_generic_shard
from axm_build.sign import (
    HYBRID1_PK_LEN,
    HYBRID1_SK_LEN,
    SUITE_HYBRID1,
    hybrid1_keygen,
)

# ---------------------------------------------------------------------------
# Config / identity
# ---------------------------------------------------------------------------

DEFAULT_SHARD_DIR = Path.home() / ".axm" / "shards"
DEFAULT_KEY_DIR = Path.home() / ".axm" / "keys"

# Genesis v1 has exactly ONE signature suite (Ed25519 ‖ ML-DSA-44, both must
# verify). There is no suite negotiation and no --suite flag anywhere.
SUITE = SUITE_HYBRID1

PUBLISHER_ID = "@axm_aide"
PUBLISHER_NAME = "axm-aide"
LICENSE_SPDX = "LicenseRef-AXM-Personal"

NS_JOURNAL = "aide/journal"
NS_TASK = "aide/task"
NS_SESSION = "aide/session"

# The evidence-tier statement stamped into every aide shard's content, so the
# limits travel with the sealed bytes and cannot be lost from a downstream copy.
EVIDENCE_TIER = "agent_record"
EVIDENCE_LIMITS: Tuple[str, ...] = (
    "caller-declared statements only",
    "not verified facts about the world",
    "tags are caller-supplied, never inferred",
    "proposals confer no authority to act; execution requires a human disposition",
    "not platform truth",
)

# The disposition vocabulary the review flow may use when it seals a
# disposition shard. It is deliberately free of "true"/"approved": a human
# escalates, dismisses, or asks for more context — the machine never decides.
DISPOSITION_PREDICATE = "disposition"
DISPOSITION_VOCAB: Tuple[str, ...] = ("escalate", "dismiss", "needs_context")


# ---------------------------------------------------------------------------
# Key pool (reimplemented from the sibling pattern — never imported from it)
# ---------------------------------------------------------------------------

# Key-material byte lengths of the RETIRED v0.x prototype suites. A pool with
# these sizes predates spec/v1 and cannot be converted.
_LEGACY_SK_LENS = {32, 2528, 3840}   # ed25519 seed | mldsa44 sk | mldsa44 sk||pk
_LEGACY_PK_LENS = {32, 1312}         # ed25519 pk   | mldsa44 pk


def get_or_create_keypair(key_dir: Path) -> bytes:
    """Load or generate the pool's axm-hybrid1 keypair.

    Genesis v1 has exactly one suite, so there is nothing to choose. On disk
    the pool is:

    - ``publisher.sk``  — the 3904-byte hybrid1 secret blob
      (ed25519 seed ‖ mldsa44 sk ‖ mldsa44 pk), exactly what
      ``CompilerConfig.private_key`` accepts.
    - ``publisher.pub`` — the 1344-byte hybrid public key
      (ed25519 pk ‖ mldsa44 pk), usable directly as an ``axm-verify``
      trusted key.

    Returns the 3904-byte secret blob.

    A legacy pool from the v0.x prototype suites CANNOT be converted and raises
    a clear error. A partial pool (exactly one of the two files) is never
    silently overwritten.
    """
    key_dir.mkdir(parents=True, exist_ok=True)
    sk_path = key_dir / "publisher.sk"
    pk_path = key_dir / "publisher.pub"

    if sk_path.exists() and pk_path.exists():
        sk_bytes = sk_path.read_bytes()
        pk_bytes = pk_path.read_bytes()
        if len(sk_bytes) == HYBRID1_SK_LEN and len(pk_bytes) == HYBRID1_PK_LEN:
            return sk_bytes
        if len(sk_bytes) in _LEGACY_SK_LENS or len(pk_bytes) in _LEGACY_PK_LENS:
            raise ValueError(
                f"Key pool at {key_dir} predates spec/v1: publisher.sk is "
                f"{len(sk_bytes)} bytes and publisher.pub is {len(pk_bytes)} bytes "
                f"(v0.x prototype material). v1 uses one suite ({SUITE_HYBRID1}) "
                f"with a {HYBRID1_SK_LEN}-byte secret blob and a {HYBRID1_PK_LEN}-byte "
                f"public key, and legacy keys cannot be converted. Move the old "
                f"pool aside:\n"
                f"    mv {key_dir} {key_dir}.v0-legacy\n"
                f"then re-run to mint a fresh v1 publisher identity. (Old shards "
                f"signed by the legacy pool are v0.x prototypes and cannot be "
                f"verified by v1 either.)"
            )
        raise ValueError(
            f"Unrecognized key material in {key_dir} "
            f"(publisher.sk={len(sk_bytes)} bytes, publisher.pub={len(pk_bytes)} bytes); "
            f"expected publisher.sk={HYBRID1_SK_LEN} and publisher.pub={HYBRID1_PK_LEN}."
        )

    if sk_path.exists() or pk_path.exists():
        raise ValueError(
            f"Incomplete key pool in {key_dir}: exactly one of publisher.sk / "
            f"publisher.pub exists. Restore the missing file or remove both "
            f"to regenerate."
        )

    public_key, secret_key = hybrid1_keygen()
    sk_path.write_bytes(secret_key)
    pk_path.write_bytes(public_key)
    return secret_key


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def short_id() -> str:
    """A short opaque id for a record (never a custody id — that is derived)."""
    return uuid.uuid4().hex[:8]


def now_rfc3339() -> str:
    """RFC 3339 UTC with the Z designator (the only form the v1 manifest accepts)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _one_line(text: str) -> str:
    """Collapse a caller string to a single line for use inside a claim literal.

    The FULL, possibly multi-line text is always sealed verbatim in content;
    only the one-line claim literal (which must be a unique source line) is
    flattened. Nothing is summarized — this is a whitespace fold, not a claim.
    """
    return " ".join(text.split())


def _dedup(items: Iterable[str]) -> List[str]:
    """Order-preserving de-duplication (identical tags would collide on span/id)."""
    seen = set()
    out: List[str] = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


# ---------------------------------------------------------------------------
# Statement / candidate construction
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _Stmt:
    """One caller-declared statement. Its evidence is its own line in source.txt."""
    text: str            # the exact source line (also the evidence string)
    subject: str         # entity label, e.g. "task/ab12cd"
    predicate: str
    obj: str             # entity label or literal value
    object_type: str     # "entity" | "literal:string" | ...
    tier: int


def _build_source_and_candidates(
    statements: Sequence[_Stmt],
    verbatim_sections: Sequence[str],
) -> Tuple[str, List[dict]]:
    """Build source.txt + flat candidate dicts.

    Structure mirrors foundry_exit/ontology_seal.py: statements are laid down
    one per line at the top (the header the claims cite), and each claim's
    evidence is bound to its own unique line. The kernel compiler
    (``_find_span_strict``) then re-derives the exact byte span. Any verbatim
    sections (the journal entry text, a proposal's full body) follow the header
    so they are sealed in the same source.txt.
    """
    lines = [s.text for s in statements]
    source = "\n".join(lines) + "\n"
    for section in verbatim_sections:
        source += "\n" + section.rstrip("\n") + "\n"

    candidates = [
        {
            "subject": s.subject,
            "predicate": s.predicate,
            "object": s.obj,
            "object_type": s.object_type,
            "tier": s.tier,
            "evidence": s.text,
        }
        for s in statements
    ]
    return source, candidates


def _aide_manifest_bytes() -> bytes:
    """The evidence-tier declaration sealed as content/aide_manifest.json."""
    return (
        json.dumps(
            {
                "evidence_tier": EVIDENCE_TIER,
                "limits": list(EVIDENCE_LIMITS),
                "custody": "genesis-derived sh1_ over the sealed manifest bytes",
                "publisher": PUBLISHER_ID,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    ).encode("utf-8")


# ---------------------------------------------------------------------------
# Seal result
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SealResult:
    shard_id: str                 # genesis-derived sh1_, the ONLY custody identity
    shard_dir: Path
    namespace: str
    record_id: str                # entry_id / task_id / session_id
    claim_count: int
    extra: dict = field(default_factory=dict)   # e.g. {"proposals": ["proposal/…"]}


# ---------------------------------------------------------------------------
# Seal core
# ---------------------------------------------------------------------------

def _seal(
    *,
    namespace: str,
    statements: Sequence[_Stmt],
    verbatim_sections: Sequence[str],
    title: str,
    shard_name: str,
    record_id: str,
    shard_dir_root: Path,
    key_dir: Path,
    created_at: str,
    extra: Optional[dict] = None,
) -> SealResult:
    """Seal one record into its own signed v1 shard via the kernel compiler."""
    shard_dir_root.mkdir(parents=True, exist_ok=True)
    out_dir = shard_dir_root / shard_name
    work = shard_dir_root / f".work_{shard_name}"
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)

    try:
        source, candidates = _build_source_and_candidates(statements, verbatim_sections)

        source_path = work / "source.txt"
        source_path.write_text(source, encoding="utf-8")

        candidates_path = work / "candidates.jsonl"
        with candidates_path.open("w", encoding="utf-8") as f:
            for c in candidates:
                f.write(json.dumps(c, ensure_ascii=False) + "\n")

        manifest_path = work / "aide_manifest.json"
        manifest_path.write_bytes(_aide_manifest_bytes())

        private_key = get_or_create_keypair(key_dir)

        cfg = CompilerConfig(
            source_path=source_path,
            candidates_path=candidates_path,
            out_dir=out_dir,
            private_key=private_key,
            publisher_id=PUBLISHER_ID,
            publisher_name=PUBLISHER_NAME,
            namespace=namespace,
            created_at=created_at,
            title=title,
            license_spdx=LICENSE_SPDX,
            # The evidence-tier declaration rides along as sealed content, so a
            # downstream reader always sees the limits with the bytes.
            extra_content=(("aide_manifest.json", manifest_path),),
        )

        try:
            ok = compile_generic_shard(cfg)
        except ValueError as exc:
            if "Ambiguous evidence" in str(exc):
                # Caller content reproduced one of this record's own claim
                # lines, so an evidence span stopped being unique. Surface a
                # clear aide-level refusal instead of a kernel traceback.
                raise ValueError(
                    f"cannot seal {shard_name}: the record text reproduces one "
                    f"of its own claim lines, making an evidence span ambiguous "
                    f"— change the text (or the record id) and retry. "
                    f"[kernel: {exc}]"
                ) from exc
            raise
        if not ok:
            raise RuntimeError(f"kernel compile produced no shard for {shard_name}")

        # Custody identity is genesis's, derived from the sealed manifest bytes.
        # The aide NEVER mints it.
        from axm_verify.crypto import derive_shard_id

        manifest_bytes = (out_dir / "manifest.json").read_bytes()
        shard_id = derive_shard_id(manifest_bytes)

        return SealResult(
            shard_id=shard_id,
            shard_dir=out_dir,
            namespace=namespace,
            record_id=record_id,
            claim_count=len(candidates),
            extra=extra or {},
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ---------------------------------------------------------------------------
# Journal
# ---------------------------------------------------------------------------

def journal_statements(
    text: str, tags: Sequence[str], entry_id: str, recorded_at: str,
) -> Tuple[List[_Stmt], List[str]]:
    """Pure builder for a journal record (no kernel needed).

    Claims:
      * ``journal/{id} recorded_at "<rfc3339>"``  (tier 0)
      * ``journal/{id} tagged "<tag>"``           (tier 1, one per CALLER tag)

    Zero tags yields zero ``tagged`` claims — tags are never inferred.
    """
    subj = f"journal/{entry_id}"
    tags = _dedup(t for t in tags if t)
    statements = [
        _Stmt(f'{subj} recorded_at "{recorded_at}"', subj, "recorded_at",
              recorded_at, "literal:string", 0),
    ]
    for t in tags:
        statements.append(
            _Stmt(f'{subj} tagged "{t}"', subj, "tagged", t, "literal:string", 1)
        )
    return statements, [text]


def seal_journal(
    text: str,
    tags: Sequence[str] = (),
    *,
    shard_dir_root: Path = DEFAULT_SHARD_DIR,
    key_dir: Path = DEFAULT_KEY_DIR,
    created_at: Optional[str] = None,
    entry_id: Optional[str] = None,
) -> SealResult:
    """Seal a journal entry.

    content/source.txt = a small header (id, recorded_at, caller tags) followed
    by the entry text verbatim (normalized per the kernel's canonical source
    rules). See ``journal_statements`` for the claim vocabulary.
    """
    entry_id = entry_id or short_id()
    recorded_at = created_at or now_rfc3339()
    statements, verbatim = journal_statements(text, tags, entry_id, recorded_at)

    return _seal(
        namespace=NS_JOURNAL,
        statements=statements,
        verbatim_sections=verbatim,
        title=f"journal {entry_id}",
        shard_name=f"aide_journal_{entry_id}",
        record_id=entry_id,
        shard_dir_root=shard_dir_root,
        key_dir=key_dir,
        created_at=recorded_at,
        extra={"tags": [s.obj for s in statements if s.predicate == "tagged"]},
    )


# ---------------------------------------------------------------------------
# Task
# ---------------------------------------------------------------------------

_VALID_STATUS = ("open", "done", "dropped")


def task_status_statements(task_id: str, status: str, seq: int = 0) -> List[_Stmt]:
    """Pure builder for a status change: ``declared_status`` plus ``status_seq``.

    ``status_seq`` (tier 0) is a per-task monotonic counter that breaks ties
    when two status shards share the same seconds-granularity ``created_at`` —
    without it, same-second ordering would be decided by shard-name sort order
    (i.e. by the status WORD), not by what actually happened last.
    """
    subj = f"task/{task_id}"
    stmts = [
        _Stmt(f'{subj} declared_status "{status}"', subj, "declared_status",
              status, "literal:string", 0),
    ]
    if seq:
        stmts.append(
            _Stmt(f'{subj} status_seq "{seq}"', subj, "status_seq",
                  str(seq), "literal:integer", 0)
        )
    return stmts


def task_add_statements(
    title: str, due: Optional[str], task_id: str,
) -> Tuple[List[_Stmt], List[str]]:
    """Pure builder for a new task (no kernel needed).

    Claims:
      * ``task/{id} has_title "<title>"``          (tier 0)
      * ``task/{id} declared_status "open"``       (tier 0, caller-declared)
      * ``task/{id} due "<date>"``                 (tier 0, optional)
    """
    subj = f"task/{task_id}"
    statements = [
        _Stmt(f'{subj} has_title "{_one_line(title)}"', subj, "has_title",
              _one_line(title), "literal:string", 0),
        _Stmt(f'{subj} declared_status "open"', subj, "declared_status",
              "open", "literal:string", 0),
    ]
    if due:
        statements.append(
            _Stmt(f'{subj} due "{due}"', subj, "due", due, "literal:string", 0)
        )
    return statements, [title]


def seal_task_add(
    title: str,
    due: Optional[str] = None,
    *,
    shard_dir_root: Path = DEFAULT_SHARD_DIR,
    key_dir: Path = DEFAULT_KEY_DIR,
    created_at: Optional[str] = None,
    task_id: Optional[str] = None,
) -> SealResult:
    """Seal a new task, born ``open`` (see ``task_add_statements``)."""
    task_id = task_id or short_id()
    ts = created_at or now_rfc3339()
    statements, verbatim = task_add_statements(title, due, task_id)

    return _seal(
        namespace=NS_TASK,
        statements=statements,
        verbatim_sections=verbatim,
        title=f"task {task_id}",
        shard_name=f"aide_task_{task_id}",
        record_id=task_id,
        shard_dir_root=shard_dir_root,
        key_dir=key_dir,
        created_at=ts,
    )


def seal_task_status(
    task_id: str,
    status: str,
    *,
    shard_dir_root: Path = DEFAULT_SHARD_DIR,
    key_dir: Path = DEFAULT_KEY_DIR,
    created_at: Optional[str] = None,
) -> SealResult:
    """Seal a status change as a NEW shard (append-only history).

    A task's current status is the ``declared_status`` from the shard with the
    latest ``created_at`` — a sealed shard is NEVER rewritten. ``status`` is one
    of open | done | dropped, caller-declared.
    """
    if status not in _VALID_STATUS:
        raise ValueError(f"status must be one of {_VALID_STATUS}, got {status!r}")
    ts = created_at or now_rfc3339()
    subj = f"task/{task_id}"

    # Monotonic per-task sequence: 1 + the number of existing status-change
    # shards for this task (the task-add shard is seq 0). Breaks same-second
    # created_at ties honestly, and keeps same-second shard dirs from
    # colliding on disk (a lost event would break the append-only story).
    seq = 1 + len(list(Path(shard_dir_root).glob(f"aide_task_{task_id}_*")))
    statements = task_status_statements(task_id, status, seq)
    # A distinct dir per event: timestamp + sequence.
    stamp = ts.replace(":", "").replace("-", "").replace("T", "").replace("Z", "")
    return _seal(
        namespace=NS_TASK,
        statements=statements,
        verbatim_sections=[],
        title=f"task {task_id} → {status}",
        shard_name=f"aide_task_{task_id}_{status}_{stamp}_{seq}",
        record_id=task_id,
        shard_dir_root=shard_dir_root,
        key_dir=key_dir,
        created_at=ts,
    )


# ---------------------------------------------------------------------------
# Session record
# ---------------------------------------------------------------------------

def session_statements(
    sid: str,
    reads: Sequence[str],
    produces: Sequence[str],
    proposals: Sequence[str],
    started_at: str,
    ended_at: str,
) -> Tuple[List[_Stmt], List[str], List[str]]:
    """Pure builder for a session record (no kernel needed).

    Returns (statements, verbatim_sections, proposal_ids). See ``seal_session``
    for the claim vocabulary. Proposal ids are fully qualified
    (``proposal/{sid}.{n}``) so two sessions never collide in aide/session.
    """
    subj = f"session/{sid}"
    statements = [
        _Stmt(f'{subj} started_at "{started_at}"', subj, "started_at",
              started_at, "literal:string", 0),
        _Stmt(f'{subj} ended_at "{ended_at}"', subj, "ended_at",
              ended_at, "literal:string", 0),
    ]
    for sh in _dedup(s for s in reads if s):
        statements.append(
            _Stmt(f'{subj} read "{sh}"', subj, "read", sh, "literal:string", 1)
        )
    for sh in _dedup(s for s in produces if s):
        statements.append(
            _Stmt(f'{subj} produced "{sh}"', subj, "produced", sh, "literal:string", 1)
        )

    verbatim_sections: List[str] = []
    proposal_ids: List[str] = []
    for n, ptext in enumerate([p for p in proposals if p and p.strip()], start=1):
        pid = f"proposal/{sid}.{n}"
        proposal_ids.append(pid)
        one_line = _one_line(ptext)
        statements.append(
            _Stmt(f"{subj} proposed {pid}", subj, "proposed", pid, "entity", 1)
        )
        statements.append(
            _Stmt(f'{pid} requires_disposition "human"', pid,
                  "requires_disposition", "human", "literal:string", 0)
        )
        statements.append(
            _Stmt(f'{pid} proposes "{one_line}"', pid, "proposes",
                  one_line, "literal:string", 1)
        )
        verbatim_sections.append(f"--- {pid} ---\n{ptext}")
    return statements, verbatim_sections, proposal_ids


def seal_session(
    *,
    reads: Sequence[str] = (),
    produces: Sequence[str] = (),
    proposals: Sequence[str] = (),
    started_at: Optional[str] = None,
    ended_at: Optional[str] = None,
    shard_dir_root: Path = DEFAULT_SHARD_DIR,
    key_dir: Path = DEFAULT_KEY_DIR,
    created_at: Optional[str] = None,
    session_id: Optional[str] = None,
) -> SealResult:
    """Seal what one agent work session did — and what it PROPOSES.

    Claims:
      * ``session/{id} started_at "<rfc3339>"``            (tier 0)
      * ``session/{id} ended_at   "<rfc3339>"``            (tier 0)
      * ``session/{id} read     "<sh1_…>"``                (tier 1, one per shard consulted)
      * ``session/{id} produced "<sh1_…>"``                (tier 1, one per shard produced)
      * ``session/{id} proposed proposal/{sid}.{n}``       (tier 1, entity)
      * ``proposal/{sid}.{n} requires_disposition "human"``(tier 0)
      * ``proposal/{sid}.{n} proposes "<one-line text>"``  (tier 1)

    A proposal is a sealed record, not an action: it carries no authority and
    ``requires_disposition "human"``. The full proposal text is sealed verbatim
    in content. Proposal ids are fully qualified (``proposal/{sid}.{n}``) so two
    sessions' proposals never collide in the shared aide/session namespace.
    """
    sid = session_id or short_id()
    now = created_at or now_rfc3339()
    started_at = started_at or now
    ended_at = ended_at or now
    statements, verbatim_sections, proposal_ids = session_statements(
        sid, reads, produces, proposals, started_at, ended_at,
    )

    return _seal(
        namespace=NS_SESSION,
        statements=statements,
        verbatim_sections=verbatim_sections,
        title=f"session {sid}",
        shard_name=f"aide_session_{sid}",
        record_id=sid,
        shard_dir_root=shard_dir_root,
        key_dir=key_dir,
        created_at=now,
        extra={"proposals": proposal_ids},
    )
