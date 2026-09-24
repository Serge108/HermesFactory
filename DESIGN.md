# Design

## Objective

Provide OpenRig-style persistent seats, topology, handoffs, refocus, and health monitoring for Hermes on native Windows.

## Architectural boundary

Hermes Factory does not wrap model APIs and does not emulate Hermes. It controls ordinary Hermes CLI processes and reads supported Hermes session/state surfaces.

```text
Operator
   |
Hermes Factory CLI / Desktop panel
   |
Factory service ─── SQLite state
   |
Hermes process supervisor
   |
Hermes profiles and sessions
```

## Initial components

### Rig loader

Loads and validates `.hermes/rig.yaml`: project identity, cwd, seats, profile bindings, roles, communication edges, and lifecycle policy.

### Seat registry

Maintains stable seat identity independently of the active Hermes session. Records the profile, role, current occupant, lifecycle state, and last verified progress.

### Process supervisor

Starts Hermes with native Windows subprocess APIs. It does not scrape an interactive terminal as the source of truth.

### Work queue

Stores structured work items with goals, acceptance criteria, owner, dependencies, status, and proof artifacts.

### Handoff service

Creates a bounded transfer packet containing objective, completed work, verified evidence, failed attempts, relevant files, unresolved decisions, and the recommended next action.

### Health monitor

Detects no-progress execution, repeated failures, missing proof, context rollover requirements, and unauthorized scope expansion.

## Explicit non-goals for MVP

- Claude Code, Codex, Pi, or OpenCode runtime adapters.
- Linux or tmux compatibility.
- Arbitrary agent-to-agent terminal typing.
- Large autonomous swarms.
- A second memory or skills system.
- Automatic approval of product, security, or destructive decisions.

## First vertical slice

Given a valid rig containing one seat, `factory up` starts one Hermes process, records its identity, `factory status` reports observed state, and `factory stop` terminates only that managed process. Tests must cover validation, quoting of Windows paths, state transitions, and stale-process reconciliation.
