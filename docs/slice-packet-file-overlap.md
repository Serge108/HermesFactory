# Do the packets of one slice write the same files?

Operator analysis for the `mechanoids-simulator` board, owner-assigned task
`slice-packet-file-overlap`. Branch `operator/slice-packet-file-overlap`, local commit only, no push.
Outcome recorded: `decision_required`. No engine source was changed.

## 1. The question, and how it was measured

The dispatcher caps the whole board at `MAX_CONCURRENT = 3` (read from
`C:\Users\Hare Krishna\AppData\Local\hermes\scripts\hermes-factory-mechanoids-dispatch.py:7`),
the rig allows `max_packets_per_slice: 6` (`F:\mechanoids-simulator\.hermes\rig.yaml:55`), and
the compiler gives two packets of one slice no edge between them unless their manifest declared a
dependency (`src/hermes_factory/compiler.py:311-321`: an implementation card with an empty
`depends_on` is parented only on the previous slice's terminal packets). Every card runs in the
same checkout - `workspace_kind: dir` with `workspace_path: F:\mechanoids-simulator`, written for
all cards by `src/hermes_factory/materialize.py:342-355`, and there is no per-packet worktree
anywhere in the pipeline. So the question is whether packets of one slice can be in flight
together in that one checkout.

Three sources are kept apart below, because they answer different questions:

- **declared intent** - the packet manifest that produced the cards: each packet's `likely_files`
  and its declared `depends_on`. The card body's Factory audit contract carries
  `changed_files_required: true`, which is a boolean flag rather than a file list, so the concrete
  declared set is the manifest's `likely_files`.
- **actual effect** - the `changed_files` recorded in the completion metadata of each packet's
  successful implementation run, and the file list of the commit object the packet's commit card
  recorded. They are shown in separate columns because they disagree for one packet.
- **current state** - which cards were running at the moment of the analysis, from a filesystem
  snapshot of the live board database.

Board database, read-only (copied before every read, so no connection ever held a write lock):

```text
C:\Users\Hare Krishna\AppData\Local\hermes\kanban\boards\mechanoids-simulator\kanban.db
```

### Exact commands

```bash
BOARD="C:/Users/Hare Krishna/AppData/Local/hermes/kanban/boards/mechanoids-simulator"
SCRATCH="C:/Users/Hare Krishna/AppData/Local/hermes/profiles/factory-operator/cache/scratch/ovl"
mkdir -p "$SCRATCH/boardro"
cp "$BOARD/kanban.db" "$SCRATCH/boardro/" && cp "$BOARD/kanban.db-wal" "$SCRATCH/boardro/" && cp "$BOARD/kanban.db-shm" "$SCRATCH/boardro/"
# every card, its compiler key, its manifest, its runs and their completion metadata -> dataset.json
python "$SCRATCH/final.py"
# run windows and concurrent pairs per slice
python "$SCRATCH/timing.py"
# declared-order width per slice
python "$SCRATCH/width.py"
# fresh live snapshot (copy again, then read the copy) + aggregates
python "$SCRATCH/aggregate.py"
# commit file lists, in the working repository, read-only
git -C F:/mechanoids-simulator show --name-only --format=%s <sha>
```

Scripts and their raw outputs live in that `SCRATCH` directory (`final.py`, `timing.py`,
`width.py`, `aggregate.py`, `dataset.json`). Every sqlite connection reads a copy; the live board
file is only ever the source of a `cp`. No board card, link, or edge was read-modified, created,
edited, archived, or completed by this session.

## 2. Per-slice results

The board holds 258 cards, of which 150 are packet cards (compiler key shape
`factory:<project>:<mission>:<slice>:packet:<packet id>:<implementation|review|commit>`), covering
50 distinct packets. Nine slices have two or more packets, all of them in the mission
`active-perception-continuation`; the other missions are archived or single-packet. `declared width`
below is the largest number of packets of that slice that the manifest's own declared order allows
in flight at the same time (1 = the manifest declares a strict chain).

### `end-to-end-acceptance`

Manifest: decomposition run 92 (task `t_76606b19`), 6 packets, declared width 3 (3 packets may be in flight at once). Verdict: `unproven`.

| packet | declared depends_on | declared likely_files | actual changed_files (implementation run) | commit | commit file list |
|---|---|---|---|---|---|
| `determinism-checkpoint-replay-e2e` | `explore-v3-fixture` | `tests/psyche_active_perception.rs`, `scenarios/psyche-explore.json`, `worlds/psyche-explore.json`, `src/psyche/cli.rs`, `src/psyche/tick2d.rs` | - | - | - |
| `explore-v3-fixture` | - | `scenarios/psyche-explore.json`, `worlds/psyche-explore.json`, `tests/psyche_active_perception.rs` | - | - | - |
| `food-preemption-e2e` | `scan-exploration-e2e`, `sector-food-discovery-e2e` | `tests/psyche_active_perception.rs`, `scenarios/psyche-explore.json`, `worlds/psyche-explore.json`, `src/psyche/psyche.rs`, `src/psyche/ontology.rs`, `src/psyche/tick2d.rs` | - | - | - |
| `full-gate-active-and-legacy` | `explore-v3-fixture`, `scan-exploration-e2e`, `sector-food-discovery-e2e`, `food-preemption-e2e`, `determinism-checkpoint-replay-e2e` | `tests/psyche_active_perception.rs`, `tests/psyche.rs`, `tests/simulation.rs`, `scenarios/psyche-cold.json`, `worlds/psyche-cold.json`, `src/psyche/inspect.rs`, `src/psyche/inspect_ui.rs` | - | - | - |
| `scan-exploration-e2e` | `explore-v3-fixture` | `tests/psyche_active_perception.rs`, `scenarios/psyche-explore.json`, `src/psyche/tick2d.rs`, `src/psyche/psyche.rs`, `src/psyche/ontology.rs` | - | - | - |
| `sector-food-discovery-e2e` | `explore-v3-fixture` | `tests/psyche_active_perception.rs`, `scenarios/psyche-explore.json`, `worlds/psyche-explore.json`, `src/psyche/vision.rs`, `src/psyche/angle.rs`, `src/psyche/tick2d.rs` | - | - | - |

| packet (implementation card) | card | status | run window |
|---|---|---|---|
| `determinism-checkpoint-replay-e2e` | t_4a13bcf8 | todo | - |
| `explore-v3-fixture` | t_32d963fa | todo | - |
| `food-preemption-e2e` | t_ce39bb36 | todo | - |
| `full-gate-active-and-legacy` | t_a24728ce | todo | - |
| `scan-exploration-e2e` | t_6eac448c | todo | - |
| `sector-food-discovery-e2e` | t_4a438f7f | todo | - |

Pairwise intersection over all packets of this slice (names, not counts); pairs whose declared and actual intersections are both empty are omitted:

| pair | declared intersection | actual intersection | both sides have a completed run |
|---|---|---|---|
| determinism-checkpoint-replay-e2e x explore-v3-fixture | `scenarios/psyche-explore.json`, `tests/psyche_active_perception.rs`, `worlds/psyche-explore.json` | not measured | no run |
| determinism-checkpoint-replay-e2e x food-preemption-e2e | `scenarios/psyche-explore.json`, `src/psyche/tick2d.rs`, `tests/psyche_active_perception.rs`, `worlds/psyche-explore.json` | not measured | no run |
| determinism-checkpoint-replay-e2e x full-gate-active-and-legacy | `tests/psyche_active_perception.rs` | not measured | no run |
| determinism-checkpoint-replay-e2e x scan-exploration-e2e | `scenarios/psyche-explore.json`, `src/psyche/tick2d.rs`, `tests/psyche_active_perception.rs` | not measured | no run |
| determinism-checkpoint-replay-e2e x sector-food-discovery-e2e | `scenarios/psyche-explore.json`, `src/psyche/tick2d.rs`, `tests/psyche_active_perception.rs`, `worlds/psyche-explore.json` | not measured | no run |
| explore-v3-fixture x food-preemption-e2e | `scenarios/psyche-explore.json`, `tests/psyche_active_perception.rs`, `worlds/psyche-explore.json` | not measured | no run |
| explore-v3-fixture x full-gate-active-and-legacy | `tests/psyche_active_perception.rs` | not measured | no run |
| explore-v3-fixture x scan-exploration-e2e | `scenarios/psyche-explore.json`, `tests/psyche_active_perception.rs` | not measured | no run |
| explore-v3-fixture x sector-food-discovery-e2e | `scenarios/psyche-explore.json`, `tests/psyche_active_perception.rs`, `worlds/psyche-explore.json` | not measured | no run |
| food-preemption-e2e x full-gate-active-and-legacy | `tests/psyche_active_perception.rs` | not measured | no run |
| food-preemption-e2e x scan-exploration-e2e | `scenarios/psyche-explore.json`, `src/psyche/ontology.rs`, `src/psyche/psyche.rs`, `src/psyche/tick2d.rs`, `tests/psyche_active_perception.rs` | not measured | no run |
| food-preemption-e2e x sector-food-discovery-e2e | `scenarios/psyche-explore.json`, `src/psyche/tick2d.rs`, `tests/psyche_active_perception.rs`, `worlds/psyche-explore.json` | not measured | no run |
| full-gate-active-and-legacy x scan-exploration-e2e | `tests/psyche_active_perception.rs` | not measured | no run |
| full-gate-active-and-legacy x sector-food-discovery-e2e | `tests/psyche_active_perception.rs` | not measured | no run |
| scan-exploration-e2e x sector-food-discovery-e2e | `scenarios/psyche-explore.json`, `src/psyche/tick2d.rs`, `tests/psyche_active_perception.rs` | not measured | no run |

Unproven: all six packets are `todo`; no completed run and no commit card. What is missing is the run history itself. The declared graph is the widest on the board: after `explore-v3-fixture` completes, `scan-exploration-e2e`, `sector-food-discovery-e2e` and `determinism-checkpoint-replay-e2e` all become eligible at once, and their declared sets all contain `tests/psyche_active_perception.rs`, `scenarios/psyche-explore.json` and two of the three also `src/psyche/tick2d.rs`. This slice is the strongest prediction of a future collision and the cheapest to observe, because its three parallel packets will run as soon as the mission reaches it.

### `exploration-decision`

Manifest: decomposition run 95 (task `t_1a148a62`), 6 packets, declared width 1 (strict chain). Verdict: `unproven`.

| packet | declared depends_on | declared likely_files | actual changed_files (implementation run) | commit | commit file list |
|---|---|---|---|---|---|
| `exploration-decision-config-and-gate` | `exploration-seeded-pick-primitive` | `src/psyche/psyche.rs`, `src/psyche/grid.rs`, `src/psyche/tick2d.rs` | - | - | - |
| `exploration-intention-rule-and-seed-persistence` | `exploration-reached-rest-and-no-free-point` | `src/psyche/ontology.rs`, `src/psyche/model.rs`, `src/psyche/events.rs`, `src/psyche/tick2d.rs`, `src/psyche/inspect.rs`, `src/psyche/tick.rs`, `tests/psyche.rs` | - | - | - |
| `exploration-reached-rest-and-no-free-point` | `exploration-target-persistence-and-repick` | `src/psyche/psyche.rs`, `src/psyche/tick2d.rs`, `src/psyche/ontology.rs`, `tests/psyche.rs` | - | - | - |
| `exploration-seeded-pick-primitive` | - | `src/psyche/explore.rs`, `src/psyche/mod.rs` | - | - | - |
| `exploration-slice-gate-and-scope-guard` | `exploration-intention-rule-and-seed-persistence` | `src/psyche/tick2d.rs`, `tests/psyche.rs`, `src/psyche/vision.rs`, `src/psyche/inspect.rs` | - | - | - |
| `exploration-target-persistence-and-repick` | `exploration-decision-config-and-gate` | `src/psyche/psyche.rs`, `src/psyche/tick2d.rs`, `src/psyche/grid.rs`, `src/psyche/ontology.rs` | - | - | - |

| packet (implementation card) | card | status | run window |
|---|---|---|---|
| `exploration-decision-config-and-gate` | t_b8da2d72 | todo | - |
| `exploration-intention-rule-and-seed-persistence` | t_e64bfda8 | todo | - |
| `exploration-reached-rest-and-no-free-point` | t_9c4b0d55 | todo | - |
| `exploration-seeded-pick-primitive` | t_456e7686 | todo | - |
| `exploration-slice-gate-and-scope-guard` | t_f5a41895 | todo | - |
| `exploration-target-persistence-and-repick` | t_c8d5513e | todo | - |

Pairwise intersection over all packets of this slice (names, not counts); pairs whose declared and actual intersections are both empty are omitted:

| pair | declared intersection | actual intersection | both sides have a completed run |
|---|---|---|---|
| exploration-decision-config-and-gate x exploration-intention-rule-and-seed-persistence | `src/psyche/tick2d.rs` | not measured | no run |
| exploration-decision-config-and-gate x exploration-reached-rest-and-no-free-point | `src/psyche/psyche.rs`, `src/psyche/tick2d.rs` | not measured | no run |
| exploration-decision-config-and-gate x exploration-slice-gate-and-scope-guard | `src/psyche/tick2d.rs` | not measured | no run |
| exploration-decision-config-and-gate x exploration-target-persistence-and-repick | `src/psyche/grid.rs`, `src/psyche/psyche.rs`, `src/psyche/tick2d.rs` | not measured | no run |
| exploration-intention-rule-and-seed-persistence x exploration-reached-rest-and-no-free-point | `src/psyche/ontology.rs`, `src/psyche/tick2d.rs`, `tests/psyche.rs` | not measured | no run |
| exploration-intention-rule-and-seed-persistence x exploration-slice-gate-and-scope-guard | `src/psyche/inspect.rs`, `src/psyche/tick2d.rs`, `tests/psyche.rs` | not measured | no run |
| exploration-intention-rule-and-seed-persistence x exploration-target-persistence-and-repick | `src/psyche/ontology.rs`, `src/psyche/tick2d.rs` | not measured | no run |
| exploration-reached-rest-and-no-free-point x exploration-slice-gate-and-scope-guard | `src/psyche/tick2d.rs`, `tests/psyche.rs` | not measured | no run |
| exploration-reached-rest-and-no-free-point x exploration-target-persistence-and-repick | `src/psyche/ontology.rs`, `src/psyche/psyche.rs`, `src/psyche/tick2d.rs` | not measured | no run |
| exploration-slice-gate-and-scope-guard x exploration-target-persistence-and-repick | `src/psyche/tick2d.rs` | not measured | no run |

Unproven: all six packets are `todo`; no completed run and no commit card. The manifest declares a strict chain (declared width 1), so the compiler already serialized this slice; declared sets nevertheless intersect on `src/psyche/tick2d.rs` in every one of the fifteen pairs.

### `food-preemption`

Manifest: decomposition run 90 (task `t_b6a15511`), 6 packets, declared width 1 (strict chain). Verdict: `unproven`.

| packet | declared depends_on | declared likely_files | actual changed_files (implementation run) | commit | commit file list |
|---|---|---|---|---|---|
| `preemption-cause-order-gate` | `preemption-out-of-sector-food` | `src/psyche/tick2d.rs`, `src/psyche/events.rs` | - | - | - |
| `preemption-clears-exploration-and-carries-target` | `preemption-intention-wiring` | `src/psyche/tick2d.rs`, `src/psyche/grid.rs` | - | - | - |
| `preemption-decision-precedence` | - | `src/psyche/psyche.rs`, `src/psyche/ontology.rs` | - | - | - |
| `preemption-food-perception-event` | `preemption-decision-precedence` | `src/psyche/events.rs`, `src/psyche/tick2d.rs` | - | - | - |
| `preemption-intention-wiring` | `preemption-food-perception-event` | `src/psyche/tick2d.rs` | - | - | - |
| `preemption-out-of-sector-food` | `preemption-clears-exploration-and-carries-target` | `src/psyche/tick2d.rs`, `src/psyche/vision.rs` | - | - | - |

| packet (implementation card) | card | status | run window |
|---|---|---|---|
| `preemption-cause-order-gate` | t_1e7dcbb8 | todo | - |
| `preemption-clears-exploration-and-carries-target` | t_f7448b73 | todo | - |
| `preemption-decision-precedence` | t_252c005d | todo | - |
| `preemption-food-perception-event` | t_217ded6d | todo | - |
| `preemption-intention-wiring` | t_894639c9 | todo | - |
| `preemption-out-of-sector-food` | t_226a5dbe | todo | - |

Pairwise intersection over all packets of this slice (names, not counts); pairs whose declared and actual intersections are both empty are omitted:

| pair | declared intersection | actual intersection | both sides have a completed run |
|---|---|---|---|
| preemption-cause-order-gate x preemption-clears-exploration-and-carries-target | `src/psyche/tick2d.rs` | not measured | no run |
| preemption-cause-order-gate x preemption-food-perception-event | `src/psyche/events.rs`, `src/psyche/tick2d.rs` | not measured | no run |
| preemption-cause-order-gate x preemption-intention-wiring | `src/psyche/tick2d.rs` | not measured | no run |
| preemption-cause-order-gate x preemption-out-of-sector-food | `src/psyche/tick2d.rs` | not measured | no run |
| preemption-clears-exploration-and-carries-target x preemption-food-perception-event | `src/psyche/tick2d.rs` | not measured | no run |
| preemption-clears-exploration-and-carries-target x preemption-intention-wiring | `src/psyche/tick2d.rs` | not measured | no run |
| preemption-clears-exploration-and-carries-target x preemption-out-of-sector-food | `src/psyche/tick2d.rs` | not measured | no run |
| preemption-food-perception-event x preemption-intention-wiring | `src/psyche/tick2d.rs` | not measured | no run |
| preemption-food-perception-event x preemption-out-of-sector-food | `src/psyche/tick2d.rs` | not measured | no run |
| preemption-intention-wiring x preemption-out-of-sector-food | `src/psyche/tick2d.rs` | not measured | no run |

Unproven: all six packets are `todo`; no completed run and no commit card. Declared width 1 (strict chain); nine of fifteen declared pairs intersect, always on `src/psyche/tick2d.rs`.

### `gaze-scan`

Manifest: decomposition run 87 (task `t_a51a275d`), 6 packets, declared width 1 (strict chain). Verdict: `unproven`.

| packet | declared depends_on | declared likely_files | actual changed_files (implementation run) | commit | commit file list |
|---|---|---|---|---|---|
| `gaze-lock-on-perceived-food` | `gaze-replay-and-checkpoint` | `src/psyche/tick2d.rs`, `src/psyche/psyche_input.rs`, `src/psyche/events.rs`, `src/psyche/ontology.rs` | - | - | - |
| `gaze-replay-and-checkpoint` | `gaze-turn-substep-and-causal-event` | `tests/psyche.rs`, `src/psyche/tick2d.rs`, `src/psyche/events.rs`, `src/psyche/cli.rs` | - | - | - |
| `gaze-scan-final-gate-and-scope-guard` | `gaze-sector-gate-over-a-sweep` | `src/psyche/tick2d.rs`, `tests/psyche.rs`, `src/psyche/psyche_input.rs`, `src/psyche/inspect.rs` | - | - | - |
| `gaze-scan-rule-and-dynamics-config` | - | `src/psyche/angle.rs`, `src/psyche/grid.rs` | - | - | - |
| `gaze-sector-gate-over-a-sweep` | `gaze-lock-on-perceived-food` | `src/psyche/tick2d.rs`, `src/psyche/vision.rs`, `src/psyche/psyche_input.rs`, `tests/psyche.rs` | - | - | - |
| `gaze-turn-substep-and-causal-event` | `gaze-scan-rule-and-dynamics-config` | `src/psyche/tick2d.rs`, `src/psyche/events.rs`, `src/psyche/ontology.rs`, `src/psyche/angle.rs` | - | - | - |

| packet (implementation card) | card | status | run window |
|---|---|---|---|
| `gaze-lock-on-perceived-food` | t_5c085136 | todo | - |
| `gaze-replay-and-checkpoint` | t_81d3675e | todo | - |
| `gaze-scan-final-gate-and-scope-guard` | t_574e3ce6 | todo | - |
| `gaze-scan-rule-and-dynamics-config` | t_906eef1f | todo | - |
| `gaze-sector-gate-over-a-sweep` | t_1834a5dd | todo | - |
| `gaze-turn-substep-and-causal-event` | t_87a85558 | todo | - |

Pairwise intersection over all packets of this slice (names, not counts); pairs whose declared and actual intersections are both empty are omitted:

| pair | declared intersection | actual intersection | both sides have a completed run |
|---|---|---|---|
| gaze-lock-on-perceived-food x gaze-replay-and-checkpoint | `src/psyche/events.rs`, `src/psyche/tick2d.rs` | not measured | no run |
| gaze-lock-on-perceived-food x gaze-scan-final-gate-and-scope-guard | `src/psyche/psyche_input.rs`, `src/psyche/tick2d.rs` | not measured | no run |
| gaze-lock-on-perceived-food x gaze-sector-gate-over-a-sweep | `src/psyche/psyche_input.rs`, `src/psyche/tick2d.rs` | not measured | no run |
| gaze-lock-on-perceived-food x gaze-turn-substep-and-causal-event | `src/psyche/events.rs`, `src/psyche/ontology.rs`, `src/psyche/tick2d.rs` | not measured | no run |
| gaze-replay-and-checkpoint x gaze-scan-final-gate-and-scope-guard | `src/psyche/tick2d.rs`, `tests/psyche.rs` | not measured | no run |
| gaze-replay-and-checkpoint x gaze-sector-gate-over-a-sweep | `src/psyche/tick2d.rs`, `tests/psyche.rs` | not measured | no run |
| gaze-replay-and-checkpoint x gaze-turn-substep-and-causal-event | `src/psyche/events.rs`, `src/psyche/tick2d.rs` | not measured | no run |
| gaze-scan-final-gate-and-scope-guard x gaze-sector-gate-over-a-sweep | `src/psyche/psyche_input.rs`, `src/psyche/tick2d.rs`, `tests/psyche.rs` | not measured | no run |
| gaze-scan-final-gate-and-scope-guard x gaze-turn-substep-and-causal-event | `src/psyche/tick2d.rs` | not measured | no run |
| gaze-scan-rule-and-dynamics-config x gaze-turn-substep-and-causal-event | `src/psyche/angle.rs` | not measured | no run |
| gaze-sector-gate-over-a-sweep x gaze-turn-substep-and-causal-event | `src/psyche/tick2d.rs` | not measured | no run |

Unproven: all six packets are `todo`; no completed run and no commit card. Declared width 1 (strict chain); eleven of fifteen declared pairs intersect, mostly on `src/psyche/tick2d.rs`.

### `observer-integration`

Manifest: decomposition run 91 (task `t_a06a2539`), 6 packets, declared width 2 (2 packets may be in flight at once). Verdict: `unproven`.

| packet | declared depends_on | declared likely_files | actual changed_files (implementation run) | commit | commit file list |
|---|---|---|---|---|---|
| `inspector-gaze-turn-annotation-and-trace-gaze-field` | `inspector-readout-signals-decision-target` | `src/psyche/inspect.rs`, `src/psyche/tick2d.rs`, `src/psyche/events.rs`, `src/psyche/ontology.rs`, `src/psyche/inspect_ui.rs` | - | - | - |
| `inspector-readout-signals-decision-target` | `observer-frame-gaze-heading-readout` | `src/psyche/inspect.rs`, `src/psyche/inspect_ui.rs`, `src/psyche/psyche_input.rs`, `src/psyche/psyche.rs` | - | - | - |
| `inspector-report-explore-and-preemption-traces` | `inspector-gaze-turn-annotation-and-trace-gaze-field` | `src/psyche/inspect_ui.rs`, `src/psyche/inspect.rs`, `tests/psyche.rs`, `src/psyche/tick2d.rs` | - | - | - |
| `live-overlay-gaze-heading-movement` | `observer-frame-gaze-heading-readout` | `src/psyche/live.rs`, `src/psyche/tick2d.rs`, `src/psyche/grid.rs` | - | - | - |
| `observer-frame-gaze-heading-readout` | - | `src/psyche/tick2d.rs`, `src/psyche/inspect.rs`, `src/psyche/inspect_ui.rs`, `src/psyche/grid.rs` | - | - | - |
| `observer-ring-bounded-and-absent-from-authority-gate` | `inspector-readout-signals-decision-target`, `inspector-gaze-turn-annotation-and-trace-gaze-field`, `live-overlay-gaze-heading-movement`, `inspector-report-explore-and-preemption-traces` | `src/psyche/inspect.rs`, `src/psyche/tick2d.rs`, `src/psyche/cli.rs`, `tests/psyche.rs` | - | - | - |

| packet (implementation card) | card | status | run window |
|---|---|---|---|
| `inspector-gaze-turn-annotation-and-trace-gaze-field` | t_b2fd6051 | todo | - |
| `inspector-readout-signals-decision-target` | t_a85b43bc | todo | - |
| `inspector-report-explore-and-preemption-traces` | t_4a4af645 | todo | - |
| `live-overlay-gaze-heading-movement` | t_96e91d43 | todo | - |
| `observer-frame-gaze-heading-readout` | t_ab813441 | todo | - |
| `observer-ring-bounded-and-absent-from-authority-gate` | t_c21e61bf | todo | - |

Pairwise intersection over all packets of this slice (names, not counts); pairs whose declared and actual intersections are both empty are omitted:

| pair | declared intersection | actual intersection | both sides have a completed run |
|---|---|---|---|
| inspector-gaze-turn-annotation-and-trace-gaze-field x inspector-readout-signals-decision-target | `src/psyche/inspect.rs`, `src/psyche/inspect_ui.rs` | not measured | no run |
| inspector-gaze-turn-annotation-and-trace-gaze-field x inspector-report-explore-and-preemption-traces | `src/psyche/inspect.rs`, `src/psyche/inspect_ui.rs`, `src/psyche/tick2d.rs` | not measured | no run |
| inspector-gaze-turn-annotation-and-trace-gaze-field x live-overlay-gaze-heading-movement | `src/psyche/tick2d.rs` | not measured | no run |
| inspector-gaze-turn-annotation-and-trace-gaze-field x observer-frame-gaze-heading-readout | `src/psyche/inspect.rs`, `src/psyche/inspect_ui.rs`, `src/psyche/tick2d.rs` | not measured | no run |
| inspector-gaze-turn-annotation-and-trace-gaze-field x observer-ring-bounded-and-absent-from-authority-gate | `src/psyche/inspect.rs`, `src/psyche/tick2d.rs` | not measured | no run |
| inspector-readout-signals-decision-target x inspector-report-explore-and-preemption-traces | `src/psyche/inspect.rs`, `src/psyche/inspect_ui.rs` | not measured | no run |
| inspector-readout-signals-decision-target x observer-frame-gaze-heading-readout | `src/psyche/inspect.rs`, `src/psyche/inspect_ui.rs` | not measured | no run |
| inspector-readout-signals-decision-target x observer-ring-bounded-and-absent-from-authority-gate | `src/psyche/inspect.rs` | not measured | no run |
| inspector-report-explore-and-preemption-traces x live-overlay-gaze-heading-movement | `src/psyche/tick2d.rs` | not measured | no run |
| inspector-report-explore-and-preemption-traces x observer-frame-gaze-heading-readout | `src/psyche/inspect.rs`, `src/psyche/inspect_ui.rs`, `src/psyche/tick2d.rs` | not measured | no run |
| inspector-report-explore-and-preemption-traces x observer-ring-bounded-and-absent-from-authority-gate | `src/psyche/inspect.rs`, `src/psyche/tick2d.rs`, `tests/psyche.rs` | not measured | no run |
| live-overlay-gaze-heading-movement x observer-frame-gaze-heading-readout | `src/psyche/grid.rs`, `src/psyche/tick2d.rs` | not measured | no run |
| live-overlay-gaze-heading-movement x observer-ring-bounded-and-absent-from-authority-gate | `src/psyche/tick2d.rs` | not measured | no run |
| observer-frame-gaze-heading-readout x observer-ring-bounded-and-absent-from-authority-gate | `src/psyche/inspect.rs`, `src/psyche/tick2d.rs` | not measured | no run |

Unproven: all six packets are `todo`; no completed run and no commit card. Declared width 2 - `live-overlay-gaze-heading-movement` is incomparable with all three inspector packets, so it can be in flight while the inspector chain runs. Fourteen of fifteen declared pairs intersect, mostly on `src/psyche/inspect.rs`, `src/psyche/inspect_ui.rs` and `src/psyche/tick2d.rs`; the incomparable pair matches on `src/psyche/grid.rs` and `src/psyche/tick2d.rs`.

### `occupancy-signals`

Manifest: decomposition run 94 (task `t_52ff3dd1`), 6 packets, declared width 3 (3 packets may be in flight at once). Verdict: `overlapping`.

| packet | declared depends_on | declared likely_files | actual changed_files (implementation run) | commit | commit file list |
|---|---|---|---|---|---|
| `occupancy-proprioception-signal` | - | `src/psyche/proprioception.rs`, `src/psyche/mod.rs`, `src/psyche/psyche_input.rs`, `src/psyche/psyche.rs`, `src/psyche/inspect.rs` | `src/psyche/proprioception.rs`, `src/psyche/mod.rs`, `src/psyche/psyche_input.rs`, `src/psyche/psyche.rs`, `src/psyche/inspect.rs`, `src/psyche/tick2d.rs` | `56ed426b93` | `src/psyche/inspect.rs`, `src/psyche/mod.rs`, `src/psyche/proprioception.rs`, `src/psyche/psyche.rs`, `src/psyche/psyche_input.rs`, `src/psyche/tick2d.rs` |
| `occupancy-proximity-organ-cap-free` | `occupancy-proximity-range-config` | `src/psyche/proximity.rs`, `src/psyche/mod.rs`, `src/psyche/vision.rs`, `src/psyche/psyche_input.rs`, `src/psyche/psyche.rs`, `src/psyche/inspect.rs` | `src/psyche/proximity.rs`, `src/psyche/mod.rs`, `src/psyche/vision.rs`, `src/psyche/psyche_input.rs`, `src/psyche/psyche.rs`, `src/psyche/inspect.rs`, `src/psyche/tick2d.rs` | - | - |
| `occupancy-proximity-range-config` | - | `src/psyche/grid.rs` | `src/psyche/grid.rs`, `src/psyche/tick2d.rs` | `d8cd68c67c` | `src/psyche/grid.rs`, `src/psyche/tick2d.rs` |
| `occupancy-query-set-and-collect-pipeline` | `occupancy-proximity-organ-cap-free`, `occupancy-proprioception-signal` | `src/psyche/explore.rs`, `src/psyche/psyche_input.rs`, `src/psyche/tick2d.rs`, `src/psyche/psyche.rs` | - | - | - |
| `occupancy-slice-gate` | `occupancy-query-set-and-collect-pipeline`, `occupancy-soundness-load-invariant`, `occupancy-proprioception-signal` | `src/psyche/proximity.rs`, `src/psyche/proprioception.rs`, `src/psyche/psyche_input.rs`, `src/psyche/grid.rs`, `src/psyche/tick2d.rs`, `tests/psyche.rs` | - | - | - |
| `occupancy-soundness-load-invariant` | `occupancy-proximity-range-config` | `src/psyche/grid.rs`, `src/psyche/cli.rs`, `src/psyche/live.rs` | `src/psyche/grid.rs`, `src/psyche/cli.rs`, `src/main.rs`, `src/psyche/tick2d.rs`, `tests/psyche.rs` | `163d3e9605` | `src/main.rs`, `src/psyche/cli.rs`, `src/psyche/grid.rs`, `src/psyche/tick2d.rs`, `tests/psyche.rs` |

| packet (implementation card) | card | status | run window |
|---|---|---|---|
| `occupancy-proprioception-signal` | t_2dd946b3 | done | 2026-09-30 00:39:27 -> 2026-09-30 00:53:23 |
| `occupancy-proximity-organ-cap-free` | t_892d6d8d | done | 2026-09-30 01:09:24 -> 2026-09-30 01:17:39 |
| `occupancy-proximity-range-config` | t_9352e30b | done | 2026-09-30 00:39:25 -> 2026-09-30 00:50:35 |
| `occupancy-query-set-and-collect-pipeline` | t_da5fc5b9 | todo | - |
| `occupancy-slice-gate` | t_32bb1e24 | todo | - |
| `occupancy-soundness-load-invariant` | t_817fdaaf | done | 2026-09-30 01:01:24 -> 2026-09-30 01:10:12 |

Pairwise intersection over all packets of this slice (names, not counts); pairs whose declared and actual intersections are both empty are omitted:

| pair | declared intersection | actual intersection | both sides have a completed run |
|---|---|---|---|
| occupancy-proprioception-signal x occupancy-proximity-organ-cap-free | `src/psyche/inspect.rs`, `src/psyche/mod.rs`, `src/psyche/psyche.rs`, `src/psyche/psyche_input.rs` | `src/psyche/inspect.rs`, `src/psyche/mod.rs`, `src/psyche/psyche.rs`, `src/psyche/psyche_input.rs`, `src/psyche/tick2d.rs` | yes |
| occupancy-proprioception-signal x occupancy-proximity-range-config | - | `src/psyche/tick2d.rs` | yes |
| occupancy-proprioception-signal x occupancy-query-set-and-collect-pipeline | `src/psyche/psyche.rs`, `src/psyche/psyche_input.rs` | - | no |
| occupancy-proprioception-signal x occupancy-slice-gate | `src/psyche/proprioception.rs`, `src/psyche/psyche_input.rs` | - | no |
| occupancy-proprioception-signal x occupancy-soundness-load-invariant | - | `src/psyche/tick2d.rs` | yes |
| occupancy-proximity-organ-cap-free x occupancy-proximity-range-config | - | `src/psyche/tick2d.rs` | yes |
| occupancy-proximity-organ-cap-free x occupancy-query-set-and-collect-pipeline | `src/psyche/psyche.rs`, `src/psyche/psyche_input.rs` | - | no |
| occupancy-proximity-organ-cap-free x occupancy-slice-gate | `src/psyche/proximity.rs`, `src/psyche/psyche_input.rs` | - | no |
| occupancy-proximity-organ-cap-free x occupancy-soundness-load-invariant | - | `src/psyche/tick2d.rs` | yes |
| occupancy-proximity-range-config x occupancy-slice-gate | `src/psyche/grid.rs` | - | no |
| occupancy-proximity-range-config x occupancy-soundness-load-invariant | `src/psyche/grid.rs` | `src/psyche/grid.rs`, `src/psyche/tick2d.rs` | yes |
| occupancy-query-set-and-collect-pipeline x occupancy-slice-gate | `src/psyche/psyche_input.rs`, `src/psyche/tick2d.rs` | not measured | no run |
| occupancy-slice-gate x occupancy-soundness-load-invariant | `src/psyche/grid.rs` | - | no |

Overlapping, and this is the only slice on the board where two packets were ever in flight at the same time. The manifest's declared order permits three packets in flight at once, with two independent roots - `occupancy-proximity-range-config` and `occupancy-proprioception-signal`, both `depends_on: []` - so the compiler gave their implementation cards no edge between them and the dispatcher started both: `t_9352e30b` ran 00:39:25 -> 00:50:35 and `t_2dd946b3` ran 00:39:27 -> 00:53:23, an eleven-minute overlap in one checkout. Their declared sets are disjoint (`src/psyche/grid.rs` versus five other files) and their declared intersection is empty, yet both recorded `src/psyche/tick2d.rs` in `changed_files` - a file neither packet declared. The commit objects confirm it independently: `d8cd68c` contains `src/psyche/grid.rs` and `src/psyche/tick2d.rs`; `56ed426` contains six files including `src/psyche/tick2d.rs`. Both commits were split cleanly (neither contains the other packet's code), so the collision happened while both workers were writing and did not survive into the commit objects this time. Two further packets of this slice completed later and also wrote `src/psyche/tick2d.rs`.

### `orientation-movement`

Manifest: decomposition run 89 (task `t_33f915c2`), 6 packets, declared width 2 (2 packets may be in flight at once). Verdict: `unproven`.

| packet | declared depends_on | declared likely_files | actual changed_files (implementation run) | commit | commit file list |
|---|---|---|---|---|---|
| `explore-and-seekfood-carried-targets` | `orientation-forward-locomotion-and-heading-follow` | `src/psyche/tick2d.rs`, `src/psyche/psyche.rs`, `src/psyche/inspect.rs`, `tests/psyche.rs`, `worlds/duel.json`, `scenarios/duel.json` | - | - | - |
| `orientation-action-event-and-replay` | `orientation-forward-locomotion-and-heading-follow` | `src/psyche/events.rs`, `src/psyche/tick2d.rs`, `src/psyche/cli.rs`, `tests/psyche.rs`, `worlds/duel.json` | - | - | - |
| `orientation-config-and-alignment-gate` | - | `src/psyche/grid.rs`, `src/psyche/angle.rs`, `src/psyche/tick2d.rs`, `scenarios/psyche-cold.json`, `tests/psyche.rs` | - | - | - |
| `orientation-forward-locomotion-and-heading-follow` | `orientation-turn-in-place` | `src/psyche/tick2d.rs`, `src/psyche/angle.rs`, `src/psyche/grid.rs`, `tests/psyche.rs` | - | - | - |
| `orientation-slice-gate` | `orientation-action-event-and-replay`, `explore-and-seekfood-carried-targets` | `src/psyche/tick2d.rs`, `tests/psyche.rs`, `worlds/duel.json`, `scenarios/psyche-cold.json`, `src/psyche/grid.rs` | - | - | - |
| `orientation-turn-in-place` | `orientation-config-and-alignment-gate` | `src/psyche/tick2d.rs`, `src/psyche/angle.rs`, `src/psyche/ontology.rs`, `src/psyche/events.rs`, `tests/psyche.rs` | - | - | - |

| packet (implementation card) | card | status | run window |
|---|---|---|---|
| `explore-and-seekfood-carried-targets` | t_b2de97eb | todo | - |
| `orientation-action-event-and-replay` | t_b74ebf8e | todo | - |
| `orientation-config-and-alignment-gate` | t_f48ea73a | todo | - |
| `orientation-forward-locomotion-and-heading-follow` | t_f916e9a5 | todo | - |
| `orientation-slice-gate` | t_151f59dd | todo | - |
| `orientation-turn-in-place` | t_16550e59 | todo | - |

Pairwise intersection over all packets of this slice (names, not counts); pairs whose declared and actual intersections are both empty are omitted:

| pair | declared intersection | actual intersection | both sides have a completed run |
|---|---|---|---|
| explore-and-seekfood-carried-targets x orientation-action-event-and-replay | `src/psyche/tick2d.rs`, `tests/psyche.rs`, `worlds/duel.json` | not measured | no run |
| explore-and-seekfood-carried-targets x orientation-config-and-alignment-gate | `src/psyche/tick2d.rs`, `tests/psyche.rs` | not measured | no run |
| explore-and-seekfood-carried-targets x orientation-forward-locomotion-and-heading-follow | `src/psyche/tick2d.rs`, `tests/psyche.rs` | not measured | no run |
| explore-and-seekfood-carried-targets x orientation-slice-gate | `src/psyche/tick2d.rs`, `tests/psyche.rs`, `worlds/duel.json` | not measured | no run |
| explore-and-seekfood-carried-targets x orientation-turn-in-place | `src/psyche/tick2d.rs`, `tests/psyche.rs` | not measured | no run |
| orientation-action-event-and-replay x orientation-config-and-alignment-gate | `src/psyche/tick2d.rs`, `tests/psyche.rs` | not measured | no run |
| orientation-action-event-and-replay x orientation-forward-locomotion-and-heading-follow | `src/psyche/tick2d.rs`, `tests/psyche.rs` | not measured | no run |
| orientation-action-event-and-replay x orientation-slice-gate | `src/psyche/tick2d.rs`, `tests/psyche.rs`, `worlds/duel.json` | not measured | no run |
| orientation-action-event-and-replay x orientation-turn-in-place | `src/psyche/events.rs`, `src/psyche/tick2d.rs`, `tests/psyche.rs` | not measured | no run |
| orientation-config-and-alignment-gate x orientation-forward-locomotion-and-heading-follow | `src/psyche/angle.rs`, `src/psyche/grid.rs`, `src/psyche/tick2d.rs`, `tests/psyche.rs` | not measured | no run |
| orientation-config-and-alignment-gate x orientation-slice-gate | `scenarios/psyche-cold.json`, `src/psyche/grid.rs`, `src/psyche/tick2d.rs`, `tests/psyche.rs` | not measured | no run |
| orientation-config-and-alignment-gate x orientation-turn-in-place | `src/psyche/angle.rs`, `src/psyche/tick2d.rs`, `tests/psyche.rs` | not measured | no run |
| orientation-forward-locomotion-and-heading-follow x orientation-slice-gate | `src/psyche/grid.rs`, `src/psyche/tick2d.rs`, `tests/psyche.rs` | not measured | no run |
| orientation-forward-locomotion-and-heading-follow x orientation-turn-in-place | `src/psyche/angle.rs`, `src/psyche/tick2d.rs`, `tests/psyche.rs` | not measured | no run |
| orientation-slice-gate x orientation-turn-in-place | `src/psyche/tick2d.rs`, `tests/psyche.rs` | not measured | no run |

Unproven: all six packets are `todo`; no completed run and no commit card. Declared width 2: `orientation-action-event-and-replay` and `explore-and-seekfood-carried-targets` are incomparable, and that pair intersects on `src/psyche/tick2d.rs`, `tests/psyche.rs` and `worlds/duel.json`. Every declared pair intersects on `src/psyche/tick2d.rs` and `tests/psyche.rs`.

### `sector-food-ordering`

Manifest: decomposition run 85 (task `t_a2542941`), 4 packets, declared width 1 (strict chain). Verdict: `overlapping`.

| packet | declared depends_on | declared likely_files | actual changed_files (implementation run) | commit | commit file list |
|---|---|---|---|---|---|
| `organ-equal-distance-winner-rule` | - | `src/psyche/vision.rs` | `src/psyche/vision.rs`, `src/psyche/tick2d.rs` | `8450820e1a` | `src/psyche/tick2d.rs`, `src/psyche/vision.rs` |
| `slice-gate-with-scope-guards` | `tick-ordered-percept-into-psyche-input` | `src/psyche/vision.rs`, `src/psyche/tick2d.rs` | `src/psyche/vision.rs` | `01ad66f78b` | `src/psyche/vision.rs` |
| `tick-ordered-percept-into-psyche-input` | `tick-tie-break-fixture-under-corrected-rule` | `src/psyche/tick2d.rs`, `src/psyche/vision.rs` | `src/psyche/tick2d.rs` | `14f3504912` | `src/psyche/tick2d.rs` |
| `tick-tie-break-fixture-under-corrected-rule` | `organ-equal-distance-winner-rule` | `src/psyche/tick2d.rs` | `src/psyche/tick2d.rs` | `d435770370` | `src/psyche/tick2d.rs` |

| packet (implementation card) | card | status | run window |
|---|---|---|---|
| `organ-equal-distance-winner-rule` | t_7f41161b | done | 2026-09-29 23:33:26 -> 2026-09-29 23:39:37 |
| `slice-gate-with-scope-guards` | t_ae744f04 | done | 2026-09-30 00:29:24 -> 2026-09-30 00:33:03 |
| `tick-ordered-percept-into-psyche-input` | t_9197c07a | done | 2026-09-30 00:13:24 -> 2026-09-30 00:20:37 |
| `tick-tie-break-fixture-under-corrected-rule` | t_8fb67cfd | done | 2026-09-29 23:49:25 -> 2026-09-29 23:55:59 |

Pairwise intersection over all packets of this slice (names, not counts); pairs whose declared and actual intersections are both empty are omitted:

| pair | declared intersection | actual intersection | both sides have a completed run |
|---|---|---|---|
| organ-equal-distance-winner-rule x slice-gate-with-scope-guards | `src/psyche/vision.rs` | `src/psyche/vision.rs` | yes |
| organ-equal-distance-winner-rule x tick-ordered-percept-into-psyche-input | `src/psyche/vision.rs` | `src/psyche/tick2d.rs` | yes |
| organ-equal-distance-winner-rule x tick-tie-break-fixture-under-corrected-rule | - | `src/psyche/tick2d.rs` | yes |
| slice-gate-with-scope-guards x tick-ordered-percept-into-psyche-input | `src/psyche/tick2d.rs`, `src/psyche/vision.rs` | - | yes |
| slice-gate-with-scope-guards x tick-tie-break-fixture-under-corrected-rule | `src/psyche/tick2d.rs` | - | yes |
| tick-ordered-percept-into-psyche-input x tick-tie-break-fixture-under-corrected-rule | `src/psyche/tick2d.rs` | `src/psyche/tick2d.rs` | yes |

Overlapping in actual effect, executed strictly serially. Declared width 1: the manifest declares one root and a chain, every implementation card after the first was parented on the previous packet's commit card, and the concurrency scan finds no overlapping implementation windows in this slice. The actual file sets still intersect - `organ-equal-distance-winner-rule` wrote `src/psyche/tick2d.rs` and `src/psyche/vision.rs`, while `tick-ordered-percept-into-psyche-input` and `tick-tie-break-fixture-under-corrected-rule` both wrote `src/psyche/tick2d.rs` - so the overlap is real in file space and was avoided only because the decomposer happened to declare the chain.

### `sector-vision-organ`

Manifest: decomposition run 84 (task `t_7cfa1113`), 4 packets, declared width 1 (strict chain). Verdict: `overlapping`.

| packet | declared depends_on | declared likely_files | actual changed_files (implementation run) | commit | commit file list |
|---|---|---|---|---|---|
| `final-gate-and-non-bypass-guard` | `realign-remaining-tick-fixtures-under-the-sector-gate` | `src/psyche/tick2d.rs`, `src/psyche/psyche_input.rs` | `src/psyche/tick2d.rs` | `4221b36a28` | `src/psyche/tick2d.rs` |
| `realign-remaining-tick-fixtures-under-the-sector-gate` | `realign-the-red-two-creature-tick-fixture` | `src/psyche/tick2d.rs`, `tests/psyche.rs`, `worlds/field.json` | `src/psyche/tick2d.rs`, `worlds/field.json` | `2af4a25305` | `src/psyche/tick2d.rs`, `worlds/field.json` |
| `realign-the-red-two-creature-tick-fixture` | `wire-per-creature-gaze-and-field-of-view` | `src/psyche/tick2d.rs` | `src/psyche/tick2d.rs` | `c5eec4fd87` | `src/psyche/tick2d.rs` |
| `wire-per-creature-gaze-and-field-of-view` | - | `src/psyche/tick2d.rs` | `src/psyche/tick2d.rs` | `e8e105c673` | `src/psyche/angle.rs`, `src/psyche/inspect.rs`, `src/psyche/psyche.rs`, `src/psyche/tick2d.rs`, `src/psyche/vision.rs` |

| packet (implementation card) | card | status | run window |
|---|---|---|---|
| `final-gate-and-non-bypass-guard` | t_393a38d6 | done | 2026-09-29 23:11:57 -> 2026-09-29 23:18:17 |
| `realign-remaining-tick-fixtures-under-the-sector-gate` | t_4bdc5509 | done | 2026-09-29 22:51:57 -> 2026-09-29 23:05:31 |
| `realign-the-red-two-creature-tick-fixture` | t_162368f6 | done | 2026-09-29 22:29:57 -> 2026-09-29 22:37:58 |
| `wire-per-creature-gaze-and-field-of-view` | t_43e12e27 | done | 2026-09-29 22:09:58 -> 2026-09-29 22:20:42 |

Pairwise intersection over all packets of this slice (names, not counts); pairs whose declared and actual intersections are both empty are omitted:

| pair | declared intersection | actual intersection | both sides have a completed run |
|---|---|---|---|
| final-gate-and-non-bypass-guard x realign-remaining-tick-fixtures-under-the-sector-gate | `src/psyche/tick2d.rs` | `src/psyche/tick2d.rs` | yes |
| final-gate-and-non-bypass-guard x realign-the-red-two-creature-tick-fixture | `src/psyche/tick2d.rs` | `src/psyche/tick2d.rs` | yes |
| final-gate-and-non-bypass-guard x wire-per-creature-gaze-and-field-of-view | `src/psyche/tick2d.rs` | `src/psyche/tick2d.rs` | yes |
| realign-remaining-tick-fixtures-under-the-sector-gate x realign-the-red-two-creature-tick-fixture | `src/psyche/tick2d.rs` | `src/psyche/tick2d.rs` | yes |
| realign-remaining-tick-fixtures-under-the-sector-gate x wire-per-creature-gaze-and-field-of-view | `src/psyche/tick2d.rs` | `src/psyche/tick2d.rs` | yes |
| realign-the-red-two-creature-tick-fixture x wire-per-creature-gaze-and-field-of-view | `src/psyche/tick2d.rs` | `src/psyche/tick2d.rs` | yes |

Overlapping in actual effect, executed strictly serially (declared width 1, no overlapping implementation windows). All four packets wrote `src/psyche/tick2d.rs`, so all six actual pairs intersect. One measurement disagreement is recorded here: `wire-per-creature-gaze-and-field-of-view` reported `changed_files: [src/psyche/tick2d.rs]` but its commit `e8e105c` contains five files (`angle.rs`, `inspect.rs`, `psyche.rs`, `tick2d.rs`, `vision.rs`), because the commit swept in the pre-existing uncommitted sector work the mission intent tells the builders to preserve. For that packet the commit file list is an upper bound on its own writes.

## 3. Aggregate

- Multi-packet slices on the board: **9**, all in `active-perception-continuation`.
- Verdicts: **disjoint 0**, **overlapping 3** (`sector-vision-organ`, `sector-food-ordering`,
  `occupancy-signals`), **unproven 6**.
- Slices with any actual-effect evidence at all: **3 of 9**. Slices whose packets have ever run
  concurrently: **1 of 9** (`occupancy-signals`).
- Distinct packets on the board: **50**; packets with a completed implementation run: **12**; the
  other **38** are unproven.
- Declared pairwise intersections: **95 of 117 pairs** intersect.
- Actual pairwise intersections (both sides measured): **16 of 26 pairs** intersect.
- Declared order already serializes some slices: declared width 1 in **5 of 9** slices, width 2 in
  **2**, width 3 in **2** (only one of those, `occupancy-signals`, has ever executed).
- Hotspots across the measured packets: `src/psyche/tick2d.rs` in **11 of 12** packets across **3**
  slices; `src/psyche/vision.rs` in **3** packets across **2** slices; then `src/psyche/mod.rs`,
  `src/psyche/psyche.rs`, `src/psyche/psyche_input.rs`, `src/psyche/inspect.rs` and
  `src/psyche/grid.rs`, each in 2 packets of `occupancy-signals`.
- The colliding file was declared by neither of the two packets that wrote it concurrently:
  `occupancy-proximity-range-config` declared `src/psyche/grid.rs`,
  `occupancy-proprioception-signal` declared five other files, and both wrote
  `src/psyche/tick2d.rs`.

## 4. Limits of the sample

The board's own run history is the entire evidence base, and it is small. Only 3 of the 9
multi-packet slices have any completed packet at all; 38 of 50 packets have never run; exactly one
slice has ever had two packets in flight simultaneously, and it is the one slice where the
collision was observed. A slice that never ran in parallel proves nothing about parallelism, so the
six `unproven` verdicts are absences of evidence, not evidence of safety - and three of those six
(`end-to-end-acceptance` at declared width 3, `observer-integration` and `orientation-movement` at
declared width 2) are exactly the slices that will start parallel packets when the mission reaches
them. Four of the nine slices already declare a partial order that permits more than one writer, so
the question is not hypothetical for them; the observed collision is the only case that has been
tested, and it failed.

## 5. The rule the numbers imply

Inside one slice, the number of writers must be **one**. The demonstration is direct: the only
slice whose manifest declared two independent roots is the only slice where two builders ran
concurrently, and both of them wrote `src/psyche/tick2d.rs` - a file that neither packet declared,
while their declared sets were disjoint. Two consequences follow from the same evidence. First,
declared file sets cannot certify disjointness: declared pairs intersect on `src/psyche/tick2d.rs`
in 95 of 117 cases, and the one file that actually collided was declared by neither writer, so an
affinity rule keyed on `likely_files` would not have prevented the observed collision. Second, the
compiler already serializes 5 of the 9 slices by honouring the decomposer's declared chain; what is
unsupervised is the case where a manifest declares two or more independent roots, which is exactly
what `occupancy-signals` did. Slices stay parallel: packets of different slices are already chained
across slices by the slice `depends_on` graph, so serializing inside a slice costs only the
parallelism the decomposer declared within it.

For the six `unproven` slices the honest reading is the third rule: the evidence is unproven for
most slices, and the cheapest way to make it proven is to let the already-materialized packets run
and re-measure. That costs nothing extra - 114 cards are `todo` and the dispatcher will run them -
and it converts `unproven` into measured `changed_files` per packet. The slices to watch are
`end-to-end-acceptance` (declared width 3, three packets sharing
`tests/psyche_active_perception.rs`) and `observer-integration` (declared width 2).

## 6. Item 3: why no compiler change was committed

The rule above does demand a construction-level fix, and the smallest one is small: in
`compile_materialized_execution_phase` (`src/hermes_factory/compiler.py:306-321`), chain every
packet whose manifest left `depends_on` empty onto the previous packet of the same slice in the
compiler's own topological order, so each slice's packets form one chain and no two can be in
flight at once. It needs no new policy field and no manifest change, and it makes the observed
collision impossible. It was not committed, because three consequences of that change are decisions
about what the Factory should be rather than diagnoses, and the task reserves them:

1. It silently overrides a trust-domain contract. The decomposer declares the packet partition and
   its `depends_on` graph; the materializer validates it. A card-level rule that re-chains packets
   the manifest left unrelated would make the compiled graph differ from the validated manifest, so
   the ownership boundary (DESIGN.md: the decomposer owns only the proposed packet partition) has to
   be restated - either the decomposer may no longer declare independent roots, or the compiler may
   not quietly contradict what it declared.
2. It changes the effective concurrency number. This mission's nine slices form a strict chain
   (`F:\mechanoids-simulator\.hermes\missions\active-perception-continuation.yaml`), so
   one-writer-per-slice makes the board single-lane and `MAX_CONCURRENT = 3` unreachable: the change
   is, in effect, a decision to run the board at 1. The task puts that number explicitly out of
   scope for this session.
3. It cannot be landed on the live board as it stands. The materializer re-verifies the live edge
   set against the planned one on every pass (`src/hermes_factory/materialize.py:356-368`) and
   reports any difference as `materialized task <id> failed authoritative contract read-back`, which
   `reconcile` raises as a failure (`src/hermes_factory/reconcile.py:4500-4509`). Adding edges to
   already-materialized cards therefore fails every subsequent reconcile pass until the board is
   replaced and the remaining mission re-materialized on the fixed revision. This one is read from
   the code path, not executed: proving it would have meant changing the engine under a live
   mission, which is the change itself.

The alternative on a different axis - keeping intra-slice parallelism and giving each packet its own
worktree, which would need a workspace policy, per-packet branches, and a merge step in the packet
commit contract - is strictly larger and changes the same ownership boundary from the other side.

The question for the owner, then, is one sentence: **should the compiler force one writer per slice
(cheap, immediate, single-lane board, and the decomposer loses the ability to declare parallel
packets), or should the pipeline keep parallel packets and give each packet its own worktree and a
merge step (larger, preserves parallelism, moves the risk from lost updates to merge conflicts)?**
The supporting numbers are the nine per-slice tables above: 0 disjoint, 3 overlapping, 6 unproven,
1 of 9 slices ever exercised in parallel, 95 of 117 declared pairs intersecting, and
`src/psyche/tick2d.rs` written by 11 of the 12 measured packets.

## 7. Current state at the moment of the analysis

Live board snapshot taken 2026-09-30T01:38:46 (fresh `cp` of `kanban.db`, `-wal`, `-shm`, then read):

- 258 cards: 114 `todo`, 98 `done`, 45 `archived`, 1 `running`.
- The single running card is `t_da5fc5b9`,
  `...:occupancy-signals:packet:occupancy-query-set-and-collect-pipeline:implementation`, started
  2026-09-30 01:33:25. It is the only writer in the checkout, so no two packets share files right
  now and nothing is concurrent at this moment.
- Earlier in this session the same slice had two packets in flight at once: the snapshot that
  prompted this task listed `t_9352e30b` and `t_2dd946b3` together, and their run windows confirm
  the eleven-minute overlap. That pair is the evidence in section 2.
- `src/psyche/tick2d.rs` in `F:\mechanoids-simulator` is the shared file every packet of this
  mission touches; the working tree there is clean apart from an untracked
  `mechanoids-simulator.tar.gz`, and HEAD is `ba9a4ed` (the commit for
  `occupancy-proximity-organ-cap-free`, whose commit card `t_2b21adf5` had not completed when the
  snapshot was taken).
- A second snapshot and re-read at 2026-09-30T01:46:23 returned the same numbers (258 cards, 114
  `todo`, 98 `done`, 45 `archived`, 1 `running`, the same card `t_da5fc5b9`), so the picture did
  not change while this report was written.

## 8. Appendix: the queries that produced every number

Run against a copy of the board database (`sqlite3`, read-only in practice: the live file is only
ever the source of a `cp`; the WAL and `-shm` are copied with it).

```sql
-- every card, its compiler key, and its card body (the body carries likely_files and the audit contract)
select id,title,status,assignee,idempotency_key,body,created_at,completed_at from tasks;

-- every run with its completion metadata (changed_files, commands, commit sha)
select id,task_id,status,outcome,metadata,error,started_at,ended_at from task_runs order by id;

-- live status counts
select status,count(*) from tasks group by status;

-- packet cards and the running card
select id,status,idempotency_key,worker_pid from tasks where idempotency_key like '%:packet:%';
select r.id, r.task_id, r.started_at from task_runs r where r.status='running';
```

Derived in Python from those rows: the manifest of each slice is the `metadata` of the latest
successful decomposition run whose `packets[].id` set equals the board's packet ids for that slice
(`source_slice`/`packets` keys); declared file sets are the manifest's `likely_files`; actual file
sets are `metadata.changed_files` of the first `outcome = 'completed'` run of the implementation
card, or of the commit card when the implementation card recorded none; commit file lists come from
`git -C F:/mechanoids-simulator show --name-only --format=%s <sha>` with the `metadata.commit` of
each commit card; concurrent pairs are computed by comparing every pair of completed implementation
run windows (`started_at < other.ended_at`); declared width is the largest antichain of the
manifest's `depends_on` order, computed over its transitive closure with at most six nodes.

Per-slice verdicts: `overlapping` when two packets of the slice have completed runs with a
non-empty actual intersection, `disjoint` when every measured pair is empty, `unproven` when no
packet of the slice has a completed run and no commit record exists.
