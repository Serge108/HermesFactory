# Backlog — beyond-plan items

Status: recorded 2026-09-29; nothing here is implemented yet.

These are findings from the live acceptance runs and the independent reviews that are **out of scope**
for the committed slice (`85a357c`, `393c3b9` in this worktree; `44b87ee`, `36264f2` in
`F:\HermesFactory`). They are recorded so they are not lost, not queued.

## 1. Proof-command rule, review round 2 (non-blocking)

- The extension separator must be exactly U+0020, so a tab between the declared command and its
  arguments is refused (false refusal, fail-closed).
- A degenerate *declared* command such as a bare interpreter stays trusted verbatim, so a slice that
  declares `bash` would trust `bash -c "..."`. The rule only ever narrows a declared command; it does
  not vet what a slice author declared.
- An authored root-level `artifacts` key is discarded as runtime envelope (unavoidable, worth one
  sentence in DESIGN.md).
- A tuple `artifacts` is refused as malformed.
- `%COMSPEC% /c ...` is accepted but cannot execute a second command.

## 2. Rig drift

Any policy change makes a previously materialized decomposition report
`decomposition task ... drifted from Factory plan`, because the plan projection embeds policy prose.
Decide whether the comparison should cover policy text at all or only identity fields (key, seat,
mission binding). Until then: one fresh board per rig, never re-point an existing board.

## 3. Silent board-level preflight

The `no_agent_activity` preflight returns early with `eligible: 0` and no explanation in the JSON or
CLI output, so a scanned blocked card that was *not* recovered looks identical to a board with no
blocked work. Consider reporting why.

## 4. Phantom slice id

A checkpoint continuation derived from a 5-segment slice source becomes its own slice id, so
`packets_per_architectural_slice` counts it under a phantom slice.

## 5. Metrics caveats

- `handle_status` reads `metadata.task_class`, which is absent on real rows.
- Per-card hydration costs ~3.8 s because `HermesClient` has no bulk reader.
- Time-to-first-command and time-to-first-mutation stay `null` until Hermes exposes authoritative
  action events; `no_material_progress` termination stays deferred until that signal exists and real
  runs show a separating threshold.

## 6. Native Hermes contracts to remember

Only `done`/`archived` parents release a child; `unlink_tasks` has no status guard; `archive_task` has
no state predicate. Any future destructive rewire must enforce those itself.

## 7. Task 1 leftovers — closed 2026-09-29

The installed-Hermes contract probes are done and recorded in
`F:\HermesFactory-live\evidence-recovery\16-contract-probes.txt`; the manifest budget is pinned by
`tests/test_decomposition.py::test_maximal_manifest_fits_the_recorded_completion_metadata_budget`.
Observed: completion metadata round-trips verbatim up to 30000 bytes through the CLI (40000 is
refused by the OS command-line limit, not by Hermes); `complete` without `--result`/`--summary` is
refused as evidence-free; `attachments --json` returns metadata only (id, filename, content_type,
size, uploaded_by, stored_path, created_at) and no subcommand returns content, which is why the
machine contract stays in run metadata; `archive <id>` removes the card from `list --json` while
`show <id> --json` still reports `status: archived`.

## 8. Qwen builder still unverified live

Every live worker so far ran the `decomposer-seat` profile, whose configured model is
`deepseek-v4.1-flash` via `opencode-go`. The original plan's claim about a self-hosted Qwen 3.8 27B
builder has not been exercised live.

## 9. Recovery cards require a skill no rig validation checks

Every reconciler-authored recovery and board-incident card is created with
`--skill hermes-native-agent-factories` (`_RECOVERY_SKILL`). A seat profile that lacks that skill
kills the worker at startup with `Error: Unknown skill(s): hermes-native-agent-factories`; the card
stays `running` with no result and no terminal event, so it looks like a hung agent rather than a
configuration error. `hermes-factory validate` should check that the coordinator seat's profile
provides the skill, and the reconciler should surface a worker that exited without a terminal event.

## 10. A worker-performed downstream `link` trips the materialization read-back

The worker-timeout recovery body tells the coordinator to "reconnect downstream children to the final
replacement review", and the finalizer requires exactly that link to exist
(`reconcile.py:3011`: `set(details[final_review]["children"]) == {rewire.child}`). But once the link
exists, the *next* pass fails in the materializer with `materialized task <id> failed authoritative
contract read-back`, because the read-back compares the live edge set with the planned one exactly
(`materialize.py:363-364`). Either the read-back must tolerate edges declared by an in-flight
recovery plan, or the reconciler must perform the link itself and the worker must only declare it.

## 11. An unfinished recovery is visible only as a materialization error

With a recovery created but not finalized, every later pass reports `partial task set already uses
planned execution idempotency keys: <ids>`. That message is the only signal that a recovery is
pending; `reconcile` should report the pending recovery explicitly.

## 12. Terminal decomposer timeout is reported as a self-referential recovery

The second decomposer timeout returns `recovery_task_ids: {"<card>": "<card>"}`, which reads like a
created recovery card. A distinct terminal outcome field would be clearer.

## 13. Harness note: per-card recovery needs a wide `no_agent_grace`

The board-level `no_agent_activity` preflight runs before per-card recovery and returns early when
nothing has run inside `policies.no_agent_grace`. Recovery classes are therefore unobservable on an
idle board unless the rig sets a wide grace (the acceptance runs used `30m`). This is correct
production behaviour; it is recorded so a future acceptance run does not mistake the preflight for a
recovery defect.

## 14. Three key-shape readers disagree about what a Factory key is

`reconcile._factory_identity` (5/6/7 segments, now slug-checked), `reconcile._checkpoint_continuation_keys`
(4/5/7), `reconcile._recovery_target` (6) and `metrics._key_identity` (4/5/6/7) each re-derive the
shape, and they already disagree: `_factory_identity` accepts keys metrics calls non-Factory, and
metrics reads a recovery-shaped six-segment key as a packet card. `FactoryTaskIdentity` also drops
the packet id, so identity comparisons cannot tell two packets of one slice apart. The fix is one
shared `factory_key_shape()` that every reader and the report use; until then, any grammar change has
to be repeated in four places.

## 15. A materialized mission suppresses the late no-agent preflight

`reconcile` skips its late `no_agent_activity` preflight whenever `materialized_missions` is
non-empty, but `materialize_completed_decompositions` records a mission for every decomposition group
whose cards are `done`, including the idempotent path that creates nothing (`materialize.py:427-431`).
On a rig-version-2 board the guard is therefore on for every pass after the first materialization, so
a blocked non-Factory card never raises the board incident. The early preflight and the loop's own
conditions still cover most idle cases, which keeps the gap narrow. The fix is to guard on the work
released this pass: have `_materialize_group` report whether it created cards and set the mission key
only when it did.

## 16. External operator circuit: known limits

The external operator circuit (`OPERATOR.md`, `hermes-factory operator`) detects a stopped engine
from cron evidence and starts a bounded repair session. Recorded limits, not queued work:

- The trigger only sees jobs declared in the profile's cron. A fault inside a
  Kanban-dispatched worker that never fails the cron job stays task-level and belongs to the
  in-rig watchdog, so the two circuits can both be silent while a specific worker is broken.
- The circuit reads an operator session's record only as a terminal signal, never as a result: it
  ends that session's lock, and whether the session fixed anything is a human question. A session
  that dies before writing `<operator root>/records/<stem>.json` leaves only its log on the record
  side, and its lock is released by the session budget or by a stopped heartbeat.
- `hermes cron incidents` and `hermes cron runs` expose no `--json`, so the module reads
  `executions.db` directly. A schema change in that table makes the trigger read as an unreadable
  evidence source: it exits non-zero and raises a cron incident, but no dedicated class explains it.
- The staleness window compares `last_run_at` from `jobs.json`. A dead scheduler and a paused
  profile are distinguished by `enabled`/`state`, but a scheduler that ticks while the gateway
  heartbeat is stale is not detected here; `hermes cron status` owns that signal.
- An operator session that starts but cannot work (provider down, budget exhausted at once) holds
  the lock for `--max-session-seconds` (two hours by default) while the loop stays paused, unless it
  heartbeats. The quiet window is opt-in because a session's own log is buffered and silent, and a
  silent lock must not be read as a dead one; the cost is therefore bounded by the session budget.
- The engine lock is a record, not a handle: it is released by the owning session's terminal record,
  its stopped heartbeat, or the session budget, but a session that dies without any of those leaves a
  second rig waiting for that lock to age out before its own session can start.
- The `unregistered` verdict is only made loud by the observer script, not by the Factory, so a
  supervisor launched without its observer reports `unregistered` and exits zero.

## 17. Packets of one slice write the same files, so a slice has one writer

Evidence: `docs/slice-packet-file-overlap.md` (branch `operator/slice-packet-file-overlap`, commit
`3f8a433`), measured on 2026-09-30 against `57a0806`.

- Of the nine slices with two or more packet cards, none is provably disjoint, three overlap in actual
  effect and six are unproven. Declared file sets cannot certify disjointness: the only slice that ran
  two packets concurrently (`occupancy-signals`, declared as two independent roots) had both builders
  write `src/psyche/tick2d.rs` for eleven minutes, and neither card declares that file.
- `src/psyche/tick2d.rs` is written by 22 of the 25 packet cards with measured `changed_files`, across
  twelve slices, so a per-slice width above one puts two writers on that file in one checkout.

Decision (owner, 2026-09-30): take the free evidence first, and fix the cause before the mission
reaches the parallelism it declares. Let the already materialized work run so the six unproven slices
become measured, and implement per-packet worktrees with a merge step before the packet's review and
commit contract before `end-to-end-acceptance` starts, where the declared width is three and three
packets share `tests/psyche_active_perception.rs`.

Rejected: chaining every packet in the compiler (it contradicts the declared partition, and because
this mission's slices form a strict chain it collapses the board to one writer, leaving
`MAX_CONCURRENT` = 3 unreachable, and landing it fails the authoritative read-back of the live board,
so the board has to be replaced); requiring the decomposer to declare a linear chain per slice (it
would remove the ability to declare genuine parallelism, and the existing manifests already violate
it).

Until per-packet worktrees land, `MAX_CONCURRENT` = 3 bounds the board rather than the intra-slice
width, so two packets of one slice can still run concurrently in one checkout, and the only reason
this has not corrupted a slice is that this mission's slices happen to be chained.
