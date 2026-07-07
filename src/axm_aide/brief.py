"""
axm_aide.brief — the morning read.

Mounts every locally sealed ``aide_*`` shard into axiom_runtime.SpectraEngine
(the ecosystem's read side) and renders a plain-text brief:

  * open tasks        — the task whose latest ``declared_status`` is ``open``
  * recent journal    — the last N entries, by id + tags + recorded_at
                        (NOT summaries — the aide interprets nothing)
  * pending proposals — proposed by a session, with no matching human
                        disposition record yet

Every shard is first verified against an OUT-OF-BAND trusted key (resolved
exactly like ``verify``: an explicit ``--trusted-key`` else the pool's
``publisher.pub``, NEVER the shard's own embedded key). A shard that does not
PASS is skipped and named, never silently included. The engine's own mandatory
constitution/Merkle gate runs on top of that at mount time.

axm-core is a soft dependency: importing/sealing works without it, but ``brief``
needs the query runtime and exits with a clear install hint when it is absent.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from axm_aide.records import (
    DEFAULT_KEY_DIR,
    DEFAULT_SHARD_DIR,
    DISPOSITION_PREDICATE,
    NS_JOURNAL,
    NS_SESSION,
    NS_TASK,
)


class BriefError(RuntimeError):
    """Raised for operator-facing failures (missing core, no trust anchor)."""


# ---------------------------------------------------------------------------
# Trusted key (out-of-band) — same precedence as `verify`
# ---------------------------------------------------------------------------

def resolve_trusted_key(explicit: Optional[str], key_dir: Path) -> Optional[Path]:
    """Locate the out-of-band publisher key that anchors verification.

    Precedence: an explicit ``--trusted-key`` path, else the pool's own
    ``<key_dir>/publisher.pub``. Never the shard's embedded ``sig/publisher.pub``:
    a shard re-signed under an attacker-minted keypair is internally
    self-consistent, so anchoring to its own key would report PASS for a forgery.
    Returns ``None`` when no out-of-band key is available.
    """
    if explicit:
        return Path(explicit)
    candidate = key_dir / "publisher.pub"
    return candidate if candidate.exists() else None


# ---------------------------------------------------------------------------
# Claim rows (label-resolved, attributed to a shard + its seal time)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _ClaimRow:
    namespace: str
    shard_id: str
    created_at: str
    subject: str        # entity label, e.g. "task/ab12cd"
    predicate: str
    object: str
    object_type: str
    tier: int


@dataclass
class Brief:
    open_tasks: List[dict] = field(default_factory=list)
    journal: List[dict] = field(default_factory=list)
    pending_proposals: List[dict] = field(default_factory=list)
    consulted: List[str] = field(default_factory=list)      # shard ids (sh1_)
    skipped: List[Tuple[str, str]] = field(default_factory=list)  # (name, reason)


# ---------------------------------------------------------------------------
# Data gathering
# ---------------------------------------------------------------------------

def _aide_shard_dirs(shard_dir: Path) -> List[Path]:
    if not shard_dir.exists():
        return []
    return sorted(
        p for p in shard_dir.iterdir()
        if p.is_dir() and p.name.startswith("aide_") and (p / "manifest.json").exists()
    )


def _manifest_created_at(shard: Path) -> str:
    try:
        m = json.loads((shard / "manifest.json").read_text(encoding="utf-8"))
        return str((m.get("metadata") or {}).get("created_at", "") or m.get("created_at", ""))
    except Exception:
        return ""


def _collect_rows(
    shard_dir: Path,
    trusted_key: Path,
) -> Tuple[List[_ClaimRow], List[str], List[Tuple[str, str]]]:
    """Verify each aide shard out of band, mount the survivors, return claim rows.

    Returns (rows, consulted_shard_ids, skipped[(name, reason)]).
    """
    try:
        from axiom_runtime.engine import SpectraEngine
    except ImportError as exc:
        raise BriefError(
            "Spectra (axm-core) is not installed, so `brief` cannot mount shards.\n"
            "  Install it:  pip install -e ./axm-core\n"
            "  or set PYTHONPATH to include axm-core/spectra/.\n"
            f"  (import error: {exc})"
        ) from exc
    from axm_verify.logic import verify_shard

    shards = _aide_shard_dirs(shard_dir)
    consulted: List[str] = []
    skipped: List[Tuple[str, str]] = []

    verified: List[Path] = []
    for s in shards:
        try:
            result = verify_shard(s, trusted_key_path=trusted_key)
        except Exception as exc:  # noqa: BLE001 - unreadable/broken shard
            skipped.append((s.name, f"verify error: {exc}"))
            continue
        if result.get("status") == "PASS":
            verified.append(s)
        else:
            skipped.append((s.name, str(result.get("status", "FAIL"))))

    if not verified:
        return [], consulted, skipped

    # Single-user CLI: Spectra's system-key requirement is for multi-tenant
    # deployments. Honor an explicit key, else dev mode.
    if not os.environ.get("SPECTRA_SYSTEM_KEY"):
        os.environ.setdefault("SPECTRA_DEV_MODE", "1")

    # A fresh, disposable runtime per brief so a removed shard never lingers as
    # a stale mount. The sealed shards themselves are never written to.
    runtime = Path(tempfile.mkdtemp(prefix="axm_aide_brief_"))
    engine = SpectraEngine(
        db_path=str(runtime / "spectra.db"),
        audit_path=str(runtime / "audit.jsonl"),
        cache_path=str(runtime / "cache"),
    )

    rows: List[_ClaimRow] = []
    for s in verified:
        created_at = _manifest_created_at(s)
        try:
            info = engine.mount(str(s), None, verify=False)
        except Exception as exc:  # noqa: BLE001
            skipped.append((s.name, f"mount failed: {exc}"))
            continue
        shard_id = info.get("shard_id", s.name)
        tables = info.get("tables") or []
        claims_tbl = next((t for t in tables if t.startswith("claims__")), None)
        entities_tbl = next((t for t in tables if t.startswith("entities__")), None)
        if not claims_tbl or not entities_tbl:
            skipped.append((s.name, "no claims/entities tables"))
            continue
        namespace = _shard_namespace(s)
        sql = (
            f'SELECT c.predicate AS predicate, e.label AS subject, '
            f'c.object AS object, c.object_type AS object_type, c.tier AS tier '
            f'FROM "{claims_tbl}" c JOIN "{entities_tbl}" e '
            f'ON c.subject = e.entity_id'
        )
        res = engine.query_json(sql)
        for r in res.get("rows", []):
            predicate, subject, obj, obj_type, tier = r
            rows.append(_ClaimRow(
                namespace=namespace, shard_id=shard_id, created_at=created_at,
                subject=subject, predicate=predicate, object=obj,
                object_type=obj_type, tier=int(tier),
            ))
        consulted.append(shard_id)

    return rows, consulted, skipped


def _shard_namespace(shard: Path) -> str:
    try:
        m = json.loads((shard / "manifest.json").read_text(encoding="utf-8"))
        return str((m.get("metadata") or {}).get("namespace", ""))
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def build_brief(
    shard_dir: Path,
    trusted_key: Path,
    last_n: int = 5,
) -> Brief:
    rows, consulted, skipped = _collect_rows(shard_dir, trusted_key)

    # ── Journal ────────────────────────────────────────────────────────────
    journals: Dict[str, dict] = {}
    for r in rows:
        if r.namespace != NS_JOURNAL:
            continue
        j = journals.setdefault(r.subject, {
            "entry": r.subject, "recorded_at": "", "tags": [], "shard_id": r.shard_id,
        })
        if r.predicate == "recorded_at":
            j["recorded_at"] = r.object
        elif r.predicate == "tagged":
            j["tags"].append(r.object)
    journal = sorted(
        journals.values(),
        key=lambda j: (j["recorded_at"], j["entry"]),
        reverse=True,
    )[:last_n]

    # ── Tasks (latest declared_status wins, by seal time) ──────────────────
    tasks: Dict[str, dict] = {}
    for r in rows:
        if r.namespace != NS_TASK:
            continue
        t = tasks.setdefault(r.subject, {
            "task": r.subject, "title": None, "due": None,
            "status": None, "status_at": "",
        })
        if r.predicate == "has_title":
            t["title"] = r.object
        elif r.predicate == "due":
            t["due"] = r.object
        elif r.predicate == "declared_status":
            # Append-only history: the assertion from the shard with the latest
            # created_at is the current status.
            if r.created_at >= t["status_at"]:
                t["status"] = r.object
                t["status_at"] = r.created_at
    open_tasks = [t for t in tasks.values() if t["status"] == "open"]
    open_tasks.sort(key=lambda t: (t["due"] or "9999", t["task"]))

    # ── Proposals + dispositions ───────────────────────────────────────────
    proposals: Dict[str, dict] = {}
    disposed: set = set()
    for r in rows:
        if r.predicate == DISPOSITION_PREDICATE:
            # A human disposition record (sealed by the review flow) — its
            # subject is the proposal it dispositions.
            disposed.add(r.subject)
        if r.namespace != NS_SESSION:
            continue
        if r.predicate == "proposes":
            proposals.setdefault(r.subject, {
                "proposal": r.subject, "text": r.object, "shard_id": r.shard_id,
                "session": r.subject.split("/", 1)[-1].split(".", 1)[0],
            })["text"] = r.object
    pending = [p for pid, p in proposals.items() if pid not in disposed]
    pending.sort(key=lambda p: p["proposal"])

    return Brief(
        open_tasks=open_tasks,
        journal=journal,
        pending_proposals=pending,
        consulted=consulted,
        skipped=skipped,
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_brief(brief: Brief, last_n: int = 5) -> str:
    lines: List[str] = []
    lines.append("axm-aide brief")
    lines.append("=" * 60)
    lines.append("")

    lines.append(f"OPEN TASKS ({len(brief.open_tasks)})")
    if brief.open_tasks:
        for t in brief.open_tasks:
            due = f"  due {t['due']}" if t.get("due") else ""
            lines.append(f"  - [{t['task']}] {t['title'] or '(no title)'}{due}")
    else:
        lines.append("  (none)")
    lines.append("")

    lines.append(f"RECENT JOURNAL (last {last_n})")
    if brief.journal:
        for j in brief.journal:
            tags = (" #" + " #".join(j["tags"])) if j["tags"] else ""
            lines.append(f"  - {j['recorded_at']}  [{j['entry']}]{tags}")
    else:
        lines.append("  (none)")
    lines.append("")
    lines.append("  (entries are listed by id, tags and time — not summarized)")
    lines.append("")

    lines.append(f"PENDING PROPOSALS ({len(brief.pending_proposals)})")
    if brief.pending_proposals:
        for p in brief.pending_proposals:
            lines.append(f"  - [{p['proposal']}] {p['text']}")
            lines.append("      requires_disposition: human "
                         "(escalate | dismiss | needs_context)")
    else:
        lines.append("  (none)")
    lines.append("")
    lines.append("  Proposals are sealed records, not actions. Nothing here has")
    lines.append("  been executed; each awaits a human disposition in the review flow.")
    lines.append("")

    if brief.skipped:
        lines.append("SKIPPED (did not verify against the out-of-band key)")
        for name, reason in brief.skipped:
            lines.append(f"  ! {name}: {reason}")
        lines.append("")

    lines.append("-" * 60)
    lines.append(f"provenance: {len(brief.consulted)} shard(s) consulted "
                 f"(verified, out-of-band key)")
    for sid in brief.consulted:
        lines.append(f"  {sid}")
    lines.append("evidence tier: agent_record — caller-declared statements, sealed")
    lines.append("and verifiable; nothing here is a verified fact about the world.")
    return "\n".join(lines)


def run_brief(
    *,
    shard_dir: Path = DEFAULT_SHARD_DIR,
    trusted_key: Optional[Path] = None,
    key_dir: Path = DEFAULT_KEY_DIR,
    last_n: int = 5,
) -> str:
    """Resolve the trust anchor, build the brief, return the rendered text."""
    resolved = resolve_trusted_key(str(trusted_key) if trusted_key else None, key_dir)
    if resolved is None:
        raise BriefError(
            "NO_TRUSTED_KEY: no out-of-band publisher key available.\n"
            f"  Supply --trusted-key <publisher.pub> (e.g. {key_dir / 'publisher.pub'}).\n"
            "  A shard is never read against its own embedded sig/publisher.pub."
        )
    if not resolved.exists():
        raise BriefError(f"trusted key not found: {resolved}")
    brief = build_brief(shard_dir, resolved, last_n=last_n)
    return render_brief(brief, last_n=last_n)
