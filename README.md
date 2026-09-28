# Hermes Factory

Windows-native control plane for persistent teams of Hermes agents.

The project adapts the useful operating ideas from OpenRig without introducing a second agent harness or requiring tmux/Linux.

## Core mapping

| Factory concept | Hermes primitive |
|---|---|
| Seat | Hermes profile plus a stable role |
| Occupant | A Kanban-dispatched Hermes session |
| Rig | One declarative rig plus one Hermes Kanban board |
| Pod | A named workflow and explicit task dependency edges |
| Mission and slice | A mission specification compiled into a Kanban task graph |
| Handoff | Kanban summaries, metadata, reviews, and attachments |
| Refocus | Complete project, mission, and slice intent in task context |
| Health | Kanban diagnostics plus deterministic proof audits |
| Recovery watchdog | Idempotent timeout recovery plus trusted graph finalization outside delegated-worker fences |

## MVP

1. Load and validate `.hermes/rig.yaml`.
2. Map stable seats to existing Hermes profiles.
3. Create or reuse one Hermes Kanban board per rig.
4. Compile mission and slice YAML into an idempotent Kanban task graph.
5. Render the complete intent chain and worker protocol into each task.
6. Use Kanban summaries, metadata, reviews, and attachments for handoffs and proof.
7. Report health from Kanban diagnostics and deterministic proof audits.

## Руководство по эксплуатации

Пошаговая инструкция с примерами находится в [OPERATIONS.md](OPERATIONS.md).

## Operator workflow

```bash
uv sync --extra dev
uv run hermes-factory validate --rig .hermes/rig.yaml
uv run hermes-factory apply --rig .hermes/rig.yaml
uv run hermes-factory mission plan .hermes/missions/my-mission.yaml --rig .hermes/rig.yaml --json
uv run hermes-factory mission apply .hermes/missions/my-mission.yaml --rig .hermes/rig.yaml --json
uv run hermes-factory status --rig .hermes/rig.yaml
uv run hermes-factory report --rig .hermes/rig.yaml
uv run hermes-factory stall-taxonomy --json
uv run hermes-factory reconcile --rig .hermes/rig.yaml --dispatch --json
hermes kanban --board <board-slug> dispatch --dry-run --json
hermes kanban --board <board-slug> watch
hermes dashboard --no-open
```

Rig version 2 adds a pre-builder decomposition phase. Each architectural slice first becomes one `decomposition` card. After every decomposition card for the mission completes with a valid manifest, the trusted Factory reconciler materializes bounded implementation/review packet pairs. Decomposers describe work; they do not create cards or mutate dependency edges. Builders start only after that read-back and materialization boundary succeeds.

The report is still a current snapshot and always exits `0`. In addition to the original board totals, it reports decomposition lead time, packet counts by architectural slice, builder first-attempt completion, progress checkpoints, hard timeouts by task class, median packet lead time, and rework after review. Task classes and packet identities come from the authoritative idempotency key, which `report` hydrates per card through the read-only record path because the installed `kanban list --json` projection omits it; a slice compiled without packet materialization counts as its own single packet, and a card whose key is not one of the compiler's shapes is never counted as a packet. A progress checkpoint is an unsuccessful terminal builder attempt whose summary or error begins with the exact `Factory progress checkpoint:` contract, and rework after review is measured over completed review runs only. Time to first command and first file mutation remain `null` in JSON and `unavailable` in text until Hermes exposes authoritative action events through the task/run inputs. Factory does not infer those values from summaries or chat text.

`hermes-factory` compiles, reconciles, and audits work; it does not keep idle interactive agents alive. Real execution is owned by the Hermes gateway dispatcher, which spawns and supervises Kanban task sessions. `reconcile` scans blocked Factory tasks and classifies each one from authoritative Hermes evidence (a timeout `gave_up` event, a review card blocked by its own reviewer with the recorded rejection prefix, or the `unknown_operational_stall` catch-all for any other block that is not an explicit human gate), creates or reuses one high-priority coordinator recovery card with a bounded structured outcome, then consumes completed structured recovery plans. Privileged `unlink`, archive, purge, verification, and follow-up dispatch run in the trusted no-agent reconciler rather than inside the delegated coordinator context. Run the command from a durable Hermes no-agent cron job for automatic recovery. `stall-taxonomy` exposes stable reason codes and their detection, authority, recovery, verification, fallback, and regression-test contracts. `status` exits non-zero when it finds critical or error findings, while `report` is a read-only metrics projection.

## Handoff rules

Each task carries the full intent chain, acceptance criteria, proof contract, and scope boundaries in its body. Completed work moves along parent edges through Kanban reviews, summaries, structured metadata, and attachments:

```text
Implementation occupant
  → completes its implementation card with summary and metadata
  → completion releases the pre-created reviewer child
  → reviewer independently reads the parent result and evidence
  → reviewer completes the review or blocks it with the review-rejection prefix and actionable findings
  → that recorded rejection lets the reconciler start an automatic correction pass
  → optional commit task creates a verified local commit without push
  → dependent slice receives parent summaries
  → coordinator or operator completes acceptance according to rig authority
```

Implementation and review results must publish structured completion metadata:

```json
{
  "changed_files": ["path/to/file.py"],
  "commands": [{"command": "uv run pytest -q", "exit_code": 0}],
  "limitations": []
}
```

Required artifacts must be uploaded as Hermes Kanban attachments. Self-reported artifact paths in completion metadata do not satisfy the proof audit.

## Constraints

- Windows-native: no tmux, WSL, Docker, or POSIX shell dependency.
- Hermes remains the only agent runtime and source of session truth.
- Persistent identity belongs to a seat, not to one model context.
- Repository artifacts and Hermes Kanban records are authoritative; agent chat is not.
- Decision authority defaults to `coordinator`: workers resolve in-scope ambiguity autonomously, choose the safest reversible or non-destructive option, and never request interim operator input. `human` creates a real operator gate only when the mission explicitly requires human acceptance.
- Automatic Git policy is local-only. Factory never pushes; rollback remains an explicit `git revert` operation.

Hermes Kanban owns queue persistence, worker dispatch, retries, claims, heartbeats, and task history. Hermes Factory must not duplicate those systems.

The coordinator inspects a failed card and repository, creates a smaller replacement graph, and returns an exact recovery plan. The reconciler validates that plan, removes only declared stale edges, verifies the resulting graph, archives and purges superseded cards, records an idempotent finalization marker, and asks native Hermes dispatch to continue released work. Delegated workers never receive unrestricted Kanban mutation authority.

## Status

The first Hermes-native vertical slice is implemented and operationally smoke-tested: rig and mission validation, deterministic Kanban compilation, implementation/review/acceptance topology, proof auditing, metrics, repeated idempotent apply, authoritative attachment proof, and disposable-board archival.
