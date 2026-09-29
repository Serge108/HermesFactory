# External operator

The Factory repairs tasks. Nothing inside the Factory repairs the Factory. Every recovery card,
board incident, and checkpoint continuation is created by the trusted engine, so an engine
defect that fails every reconcile pass produces no card at all: the loop stops, the board stands
still, and no stall class fires. `stall_taxonomy` classifies the Factory's clients, not the
Factory.

This document defines the external half of that split. It is operational, not aspirational: the
trigger, the authority, and the exit conditions are all implemented.

## Ownership

Coordinator repairs tasks inside a mission. Reconciler applies deterministic recovery inside a
board. Hermes owns task state, dispatch, retries, and events. The external operator repairs the
engine and controls the loop that runs it. The operator is not a seat: a seat is dispatched by
the dispatcher being repaired, so the operator runs outside the board, with the repository as
its workspace.

## Trigger

Every input comes from outside the board, because the board is an output of the engine being
watched:

- the Hermes cron job list (`<profile>/cron/jobs.json`): `enabled`, `state`, `last_status`,
  `failure_streak`, `last_run_at`;
- the run records (`<profile>/cron/executions.db`, table `executions`): `status`, `started_at`,
  `finished_at`;
- the durable cron failure incidents (`<profile>/cron/executions.db`, table `cron_incidents`):
  `state`, `error`, `first_seen_at`, `last_seen_at`.

The run records are not redundant with `last_run_at`. Hermes writes `last_run_at` when a run
finishes, so a job whose pass takes minutes looks stale while it is working: in the live
deployment the reconcile job reported an 856-second-old `last_run_at` while its newest execution
had started 541 seconds earlier. Staleness is therefore computed from the newest authoritative
activity, and a `running` execution keeps the loop alive on its own.

`hermes-factory operator assess` classifies the loop from that evidence alone.

| Verdict | Condition | Launches a session |
| --- | --- | --- |
| `healthy` | every bound job below the streak threshold, last status not `failed`, last run inside the staleness window | no |
| `degraded` | a bound job failed fewer times than the threshold, or its last run time is unreadable | no |
| `paused` | a bound job is disabled or not `scheduled` | no |
| `stopped` | a bound job reached the streak threshold, or an enabled job has not run inside the staleness window | yes |
| `unregistered` | no cron job is bound to the project | no |

Precedence is `paused` over `stopped`: pausing the watchdog is how the loop is taken down for a
repair, so a paused job is declared intent, the same way a configured human gate is declared
intent. A self-healed incident (state `resolved`, or still unacknowledged while the job
succeeds) is reported as evidence and never launches anything; the live history shows multi-tick
failure streaks that closed on their own, and those must not call an operator.

Jobs whose name starts with `Hermes Factory operator:` are excluded from binding, so a
supervision job never supervises itself.

## Authority

The operator session may change the engine: source, tests, documentation, and a local commit on
a branch. It may pause and resume the cron jobs. It may read the wedged board.

It may not mutate any task, link, or edge on any board; push to a remote; rewrite history;
publish secrets; accept a mission; or edit the tests that judge its own change. The board stays
Hermes's truth and the coordinator's workspace. When a repair forces a manual card edit, the
mission's evidence is no longer Factory evidence and must be reported as repaired by hand.

## Conditions

One session, one bounded defect, three acceptable terminal outcomes: `fixed`, `diagnosed`, or
`decision_required`. Requiring `fixed` as the only success condition buys plausible patches, so
a narrowed defect with a failing test and a reproduction is a complete result, and a question
about what the Factory should be is escalated rather than guessed.

A repair is accepted only against a pristine oracle: the acceptance run uses the test tree as it
existed at the starting revision, in a separate worktree, with the changed source copied in. A
suite the session could edit proves nothing about the suite the session did not edit.

The engine is never hot-patched under a live mission. The mission keeps the revision it started
with; a fix applies to the next mission. When the board cannot continue on the fixed revision,
it is replaced, not repaired: export it as the record, archive it, re-apply the mission files to
a fresh board, and report the re-run cost.

## Artifacts

- lock: `<operator root>/<project>.operator.lock.json`, holding the start time, the incident ids,
  and the session log path. Process liveness is deliberately not probed: on Windows
  `os.kill(pid, 0)` raises for a live process and returns quietly for a dead one, so it cannot
  distinguish them. The lock is live while the session's log keeps being written inside the
  quiet window and the lock age is inside the maximum session age.
- log: `<operator root>/logs/<project>-operator-<started_at>.log`, the session's own output.
- record: `<operator root>/records/<same stem>.json`, written by the session before it exits.

`hermes-factory operator supervise` is idempotent per lock: while a session owns the lock, later
ticks report `operator_session_already_running` and launch nothing. A lock whose session log
stopped changing is released and a fresh session starts, which is how a crashed session is
recovered without a human.

## Why this is not a rig policy

The trigger is a property of the deployment, not of a mission. Putting it in the rig would
change the compiled plan projection, and any policy change makes previously materialized
decompositions report as drifted from the Factory plan, so a watchdog setting could wedge a
running board. The loop configuration therefore lives on the command line and in the cron job
that calls it, and no rig version is required.

## Commands

```text
hermes-factory operator assess --project <slug> [--job <job-id>]... [--json]
hermes-factory operator supervise --project <slug> --prompt <file> --repo <path>
    [--job <job-id>]... [--profile <cron-profile>] [--operator-profile <profile>]
    [--budget <n>] [--log-directory <path>] [--lock-directory <path>]
    [--threshold <n>] [--stale-after <seconds>] [--quiet-seconds <seconds>]
    [--max-session-seconds <seconds>] [--dry-run] [--json]
```

Both commands exit zero for every verdict, including `stopped`: a watchdog that fails when it
finds a failure would raise a cron incident for itself on every tick. They exit non-zero only
when the evidence itself is unreadable, which is a real fault worth an incident.

`supervise` never mutates a board and never resumes a job. Resuming is the session's own step,
after it has a verified fix, which is what keeps "who restarted the loop" answerable.
