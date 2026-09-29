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
