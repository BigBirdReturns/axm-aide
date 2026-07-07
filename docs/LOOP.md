# The loop

axm-aide is built to be the memory-and-proposal spoke of a **scheduled agent session**. The agent does not act on the world through the aide. It reads context, does its work through whatever tools it legitimately has, and then *records* what it did and *proposes* what it thinks should happen next. A human closes the loop.

```
        ┌─────────────────────────────────────────────────────────────┐
        │                                                             │
        ▼                                                             │
  1. READ ──────────► 2. WORK ──────────► 3. RECORD ──────────► 4. REVIEW
  axm-aide brief      (the agent's own    axm-aide session       a human dispositions
  (open tasks,        tools; the aide      record --read …        each proposal in the
   journal, pending    executes nothing)   --produced …           console: escalate /
   proposals)                              --propose "…"          dismiss / needs_context
                                                                       │
                                                                       └──► seals a
                                                                            disposition
                                                                            shard the next
                                                                            brief will see
```

## 1. Read — `axm-aide brief`

The morning read. It mounts every locally sealed `aide_*` shard into axm-core's SpectraEngine (after verifying each against the out-of-band publisher key) and prints:

- **open tasks** — the tasks whose latest `declared_status` is `open`;
- **recent journal** — the last N entries by id, tags, and time (never summarized);
- **pending proposals** — proposals a prior session made that have no matching human disposition yet.

The brief is plain text with a provenance footer naming every shard id consulted. That footer is the agent's grounding: it worked from these exact, verifiable records and nothing else.

## 2. Work

The agent does its actual job with its own authorized tools. **The aide is not in this path.** It cannot email, deploy, purchase, or delete. If the work implies an action the agent cannot or should not take unilaterally, that action becomes a *proposal* in step 3 — it is never executed by the aide.

## 3. Record — `axm-aide session record`

At the end of the session, seal exactly one session shard describing what happened:

```bash
axm-aide session record \
  --read sh1_<a-shard-the-session-consulted> \
  --read sh1_<another> \
  --produced sh1_<a-shard-the-session-created> \
  --propose "Email the Q3 summary to finance@example.com" \
  --propose "Close task task/ab12cd — the report shipped"
```

- `--read` / `--produced` are repeatable and record the shard ids the session consulted and created (tier-1 caller assertions).
- `--propose` is repeatable. Each proposal is sealed **verbatim** and carries `requires_disposition "human"` (tier 0). It confers no authority. Nothing is executed.

Every seal prints the derived `sh1_` and the shard directory.

## 4. Review — the human disposition

A human reads the pending proposals (in the `brief`, or in the AXM console) and dispositions each one. The vocabulary is deliberately not `approve`/`true`:

| Disposition | Meaning |
|---|---|
| `escalate` | act on it / route it onward for action |
| `dismiss` | no, drop it |
| `needs_context` | not enough to decide — send it back |

The review flow seals a **disposition shard** whose claim `proposal/{sid}.{n} disposition "<escalate\|dismiss\|needs_context>"` (tier 0, human-declared) references the proposal. The next `brief` treats any proposal that has a matching `disposition` claim as no longer pending. (v0 of the aide writes no dispositions itself — the machine never decides; the console does, on a human's behalf.)

---

## Copy-pasteable session prompt template

Give this to the scheduled agent as its standing instruction:

```
You are a scheduled work session operating under the AXM aide doctrine.

BEFORE you do anything:
  1. Run `axm-aide brief` and read it in full. Note the shard ids in the
     provenance footer — those are your grounded context. Do not invent facts
     not traceable to them.

DO YOUR WORK:
  2. Address the open tasks and any pending proposals using ONLY your own
     authorized tools. You may NOT act through the aide. If a needed action is
     one you cannot or should not take unilaterally (sending mail, spending
     money, deleting data, publishing), DO NOT DO IT — turn it into a proposal.

WHEN YOU FINISH, record the session with ONE command:
  3. `axm-aide session record` with:
       --read sh1_…       for every shard you consulted (from the brief footer)
       --produced sh1_…   for every shard you sealed this session
       --propose "…"      one clear, single-line proposal per action you want a
                          human to disposition. State the action and its target
                          plainly. Do not phrase a proposal as done or approved.

NEVER:
  - claim a proposal is approved, true, or executed — it is a request awaiting a
    human disposition (escalate / dismiss / needs_context);
  - infer or attach tags/statuses the caller did not supply;
  - mint or guess a shard id — the kernel derives every sh1_.

A human will review your proposals and disposition them. That is the only path
by which anything you propose becomes an action.
```

## Trust-model note (v0, stated plainly)

In v0 the proposer and the disposer share one key pool (`~/.axm/keys`). The
aide structurally cannot seal a `disposition` claim — no aide verb emits that
predicate — but the review flow's dispositions are not yet *cryptographically*
distinguished from the aide's own records. A future version should give the
human reviewer a separate keypair so a disposition is provably not
self-issued. Until then, the separation is enforced by vocabulary and code
path, not by keys. Declared, not hidden.
