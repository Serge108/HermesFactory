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

## 7. Task 1 leftovers

The installed-Hermes contract probes (completion-metadata budget, attachment content retrieval,
archive semantics) are being pinned as fixtures in `tests/test_hermes_cli.py`; remove this item once
that lands.

## 8. Qwen builder still unverified live

Every live worker so far ran the `decomposer-seat` profile, whose configured model is
`deepseek-v4.1-flash` via `opencode-go`. The original plan's claim about a self-hosted Qwen 3.8 27B
builder has not been exercised live.
