"""
axm_aide.cli — command-line interface for the aide spoke.

Verbs:
    axm-aide journal "text" [--tag T ...]
    axm-aide task add "title" [--due D]
    axm-aide task done <task_id>
    axm-aide task list
    axm-aide session record [--read sh1_… ...] [--produced sh1_… ...] [--propose "text" ...]
    axm-aide brief [--trusted-key PATH]
    axm-aide verify [SHARD]

Also registers as an axm.spokes plugin so `axm aide …` works once axm-core
discovers the installed spoke.

Doctrine: the CLI writes only caller-declared statements, never mints a
custody id, and executes nothing — `session record` seals proposals that
require a human disposition; it does not act on them.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional, Tuple

import click

from axm_aide.records import (
    DEFAULT_KEY_DIR,
    DEFAULT_SHARD_DIR,
    NS_TASK,
    SealResult,
    seal_journal,
    seal_session,
    seal_task_add,
    seal_task_status,
)


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _c(code: str, msg: str) -> str:
    return f"\033[{code}m{msg}\033[0m"

def info(msg: str) -> None: click.echo(_c("37", msg))
def ok(msg: str) -> None: click.echo(_c("32", msg))
def warn(msg: str) -> None: click.echo(_c("33", msg))
def err(msg: str) -> None: click.echo(_c("31", msg), err=True)
def dim(msg: str) -> None: click.echo(_c("90", msg))
def head(msg: str) -> None: click.echo(_c("36", msg))


def _report_seal(kind: str, res: SealResult) -> None:
    """Every seal prints the derived sh1_ and the shard dir."""
    ok(f"  ✓ sealed {kind}: {res.record_id}")
    info(f"    shard_id: {res.shard_id}")
    info(f"    shard:    {res.shard_dir}")
    dim(f"    ({res.claim_count} claim(s), namespace {res.namespace})")


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------

@click.group()
def aide_group():
    """axm aide — a sovereign assistant: custody of memory, surrender of judgment."""
    pass


# Standalone entry point (axm-aide)
main = aide_group


# ---------------------------------------------------------------------------
# journal
# ---------------------------------------------------------------------------

@aide_group.command("journal")
@click.argument("text")
@click.option("--tag", "tags", multiple=True, help="Caller-supplied tag (repeatable). Never inferred.")
@click.option("--out", default=None, help="Shard directory (default: ~/.axm/shards/)")
def cmd_journal(text: str, tags: Tuple[str, ...], out: Optional[str]):
    """Seal a journal entry, verbatim, with any caller-supplied tags.

    Tags are recorded exactly as given; the aide never derives a tag from the
    entry text. Zero tags means zero `tagged` claims.
    """
    shard_dir = Path(out) if out else DEFAULT_SHARD_DIR
    res = seal_journal(text, tags=tags, shard_dir_root=shard_dir)
    _report_seal("journal", res)


# ---------------------------------------------------------------------------
# task
# ---------------------------------------------------------------------------

@aide_group.group("task")
def task_group():
    """Task tracking. Status is caller-declared; history is append-only."""
    pass


@task_group.command("add")
@click.argument("title")
@click.option("--due", default=None, help="Optional caller-declared due date")
@click.option("--out", default=None, help="Shard directory (default: ~/.axm/shards/)")
def cmd_task_add(title: str, due: Optional[str], out: Optional[str]):
    """Seal a new task (born `open`)."""
    shard_dir = Path(out) if out else DEFAULT_SHARD_DIR
    res = seal_task_add(title, due=due, shard_dir_root=shard_dir)
    _report_seal("task", res)


@task_group.command("done")
@click.argument("task_id")
@click.option("--out", default=None, help="Shard directory (default: ~/.axm/shards/)")
def cmd_task_done(task_id: str, out: Optional[str]):
    """Declare a task done — sealed as a NEW shard (the original is never rewritten)."""
    shard_dir = Path(out) if out else DEFAULT_SHARD_DIR
    res = seal_task_status(task_id, "done", shard_dir_root=shard_dir)
    _report_seal("task status → done", res)


@task_group.command("drop")
@click.argument("task_id")
@click.option("--out", default=None, help="Shard directory (default: ~/.axm/shards/)")
def cmd_task_drop(task_id: str, out: Optional[str]):
    """Declare a task dropped — sealed as a NEW shard (append-only history)."""
    shard_dir = Path(out) if out else DEFAULT_SHARD_DIR
    res = seal_task_status(task_id, "dropped", shard_dir_root=shard_dir)
    _report_seal("task status → dropped", res)


@task_group.command("list")
@click.option("--out", "shards", default=None, help="Shard directory (default: ~/.axm/shards/)")
def cmd_task_list(shards: Optional[str]):
    """List tasks with their current (latest declared) status.

    Reads shard manifests + graphs directly (no query runtime needed), so it
    works with just the kernel installed.
    """
    shard_dir = Path(shards) if shards else DEFAULT_SHARD_DIR
    if not shard_dir.exists():
        info(f"No shards directory at {shard_dir}")
        return

    # Aggregate task claims straight from each shard's sealed graph tables.
    tasks: dict = {}
    for sp in sorted(shard_dir.iterdir()):
        if not (sp.is_dir() and sp.name.startswith("aide_task_") and (sp / "manifest.json").exists()):
            continue
        try:
            m = json.loads((sp / "manifest.json").read_text())
            if str((m.get("metadata") or {}).get("namespace", "")) != NS_TASK:
                continue
            created_at = str((m.get("metadata") or {}).get("created_at", ""))
            labels = {r["entity_id"]: r["label"]
                      for r in _read_jsonl(sp / "graph" / "entities.jsonl")}
            claims = list(_read_jsonl(sp / "graph" / "claims.jsonl"))
            # Same-second ties break on the shard's status_seq claim (0 when
            # absent), never on shard-name sort order.
            try:
                seq = next(int(c["object"]) for c in claims
                           if c["predicate"] == "status_seq")
            except StopIteration:
                seq = 0
            key = (created_at, seq)
            for c in claims:
                subj = labels.get(c["subject"], c["subject"])
                t = tasks.setdefault(subj, {"title": None, "due": None,
                                            "status": None, "status_key": ("", -1)})
                if c["predicate"] == "has_title":
                    t["title"] = c["object"]
                elif c["predicate"] == "due":
                    t["due"] = c["object"]
                elif c["predicate"] == "declared_status" and key >= t["status_key"]:
                    t["status"] = c["object"]
                    t["status_key"] = key
        except Exception as e:  # noqa: BLE001
            warn(f"  {sp.name}: unreadable ({e})")

    if not tasks:
        info("No tasks yet.  Run: axm-aide task add \"…\"")
        return

    head(f"\n{len(tasks)} task(s):\n")
    for subj, t in sorted(tasks.items()):
        mark = {"open": "○", "done": "✓", "dropped": "✗"}.get(t["status"], "?")
        due = f"  due {t['due']}" if t["due"] else ""
        line = f"  {mark} {t['status'] or '?':<8} [{subj}] {t['title'] or ''}{due}"
        (ok if t["status"] == "open" else dim)(line)
    click.echo()


def _read_jsonl(path: Path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            yield json.loads(line)


# ---------------------------------------------------------------------------
# session record
# ---------------------------------------------------------------------------

@aide_group.command("session")
@click.argument("verb", type=click.Choice(["record"]))
@click.option("--read", "reads", multiple=True, help="sh1_ shard consulted (repeatable)")
@click.option("--produced", "produces", multiple=True, help="sh1_ shard produced (repeatable)")
@click.option("--propose", "proposals", multiple=True,
              help="One proposal (repeatable). A sealed record requiring a human disposition — never executed.")
@click.option("--started-at", default=None, help="RFC3339 start time (default: now)")
@click.option("--ended-at", default=None, help="RFC3339 end time (default: now)")
@click.option("--out", default=None, help="Shard directory (default: ~/.axm/shards/)")
def cmd_session(verb: str, reads: Tuple[str, ...], produces: Tuple[str, ...],
                proposals: Tuple[str, ...], started_at: Optional[str],
                ended_at: Optional[str], out: Optional[str]):
    """Record what an agent work session did — and what it PROPOSES.

    Builds ONE session shard: what it read, what it produced, and each proposal
    (sealed verbatim, requiring a human disposition). The aide records; it does
    not act.
    """
    shard_dir = Path(out) if out else DEFAULT_SHARD_DIR
    res = seal_session(
        reads=reads, produces=produces, proposals=proposals,
        started_at=started_at, ended_at=ended_at, shard_dir_root=shard_dir,
    )
    _report_seal("session", res)
    for pid in res.extra.get("proposals", []):
        dim(f"    proposed: {pid}  (requires human disposition)")


# ---------------------------------------------------------------------------
# brief
# ---------------------------------------------------------------------------

@aide_group.command("brief")
@click.option("--out", "shards", default=None, help="Shard directory (default: ~/.axm/shards/)")
@click.option("--trusted-key", "trusted_key_opt", default=None,
              help="Out-of-band publisher key (1344-byte hybrid). Defaults to the "
                   "pool's publisher.pub. Never the shard's own embedded key.")
@click.option("--last", "last_n", default=5, type=int, help="How many recent journal entries")
def cmd_brief(shards: Optional[str], trusted_key_opt: Optional[str], last_n: int):
    """The morning read: open tasks, recent journal, pending proposals."""
    from axm_aide.brief import run_brief, BriefError

    shard_dir = Path(shards) if shards else DEFAULT_SHARD_DIR
    try:
        text = run_brief(
            shard_dir=shard_dir,
            trusted_key=Path(trusted_key_opt) if trusted_key_opt else None,
            key_dir=DEFAULT_KEY_DIR,
            last_n=last_n,
        )
    except BriefError as e:
        err(str(e))
        # NO_TRUSTED_KEY is a refusal to proceed without a trust anchor.
        sys.exit(2 if str(e).startswith("NO_TRUSTED_KEY") else 1)
    click.echo(text)


# ---------------------------------------------------------------------------
# verify  (out-of-band key precedence + frozen exit codes 0/1/2)
# ---------------------------------------------------------------------------

def _resolve_trusted_key(explicit: Optional[str], key_dir: Path) -> Optional[Path]:
    """Out-of-band key precedence: explicit --trusted-key, else pool publisher.pub.

    Never the shard's own embedded sig/publisher.pub — anchoring to that reports
    PASS for a shard re-signed under an attacker-minted keypair.
    """
    if explicit:
        return Path(explicit)
    candidate = key_dir / "publisher.pub"
    return candidate if candidate.exists() else None


@aide_group.command("verify")
@click.argument("shard_id", required=False)
@click.option("--out", "shards", default=None, help="Shard directory (default: ~/.axm/shards/)")
@click.option("--trusted-key", "trusted_key_opt", default=None,
              help="Out-of-band publisher public key. Defaults to the pool's "
                   "publisher.pub. Never the shard's own embedded key.")
def cmd_verify(shard_id: Optional[str], shards: Optional[str], trusted_key_opt: Optional[str]):
    """Verify shard integrity (Merkle root + signature) against an out-of-band key.

    Exit codes are frozen: 0 all PASS, 1 a FAIL, 2 no trust anchor / bad input.
    """
    trusted_key = _resolve_trusted_key(trusted_key_opt, DEFAULT_KEY_DIR)
    if trusted_key is None:
        err("  ✗ NO_TRUSTED_KEY  no out-of-band publisher key available")
        err(f"  Supply --trusted-key <publisher.pub> (e.g. {DEFAULT_KEY_DIR / 'publisher.pub'})")
        err("  A shard is never verified against its own embedded sig/publisher.pub.")
        sys.exit(2)
    if not trusted_key.exists():
        err(f"  ✗ trusted key not found: {trusted_key}")
        sys.exit(2)

    from axm_verify.logic import verify_shard

    shard_dir = Path(shards) if shards else DEFAULT_SHARD_DIR
    if not shard_dir.exists():
        err(f"No shards directory at {shard_dir}")
        sys.exit(2)

    candidates = sorted(
        p for p in shard_dir.iterdir()
        if p.is_dir() and (p / "manifest.json").exists()
    )
    if shard_id:
        candidates = [p for p in candidates if p.name.startswith(shard_id)]
        if not candidates:
            err(f"No shard matching '{shard_id}'")
            sys.exit(1)

    info(f"trusted key: {trusted_key}")
    all_pass = True
    for s in candidates:
        try:
            result = verify_shard(s, trusted_key_path=trusted_key)
            manifest = json.loads((s / "manifest.json").read_text())
            title = manifest.get("metadata", {}).get("title", s.name)
            if result.get("status") == "PASS":
                ok(f"  ✓ PASS  {title[:60]}")
            else:
                err(f"  ✗ FAIL  {title[:60]}")
                for e in result.get("errors", []):
                    err(f"         [{e.get('code', '?')}] {e.get('message', '')}")
                all_pass = False
        except Exception as e:  # noqa: BLE001
            err(f"  ✗ ERROR {s.name}: {e}")
            all_pass = False

    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
