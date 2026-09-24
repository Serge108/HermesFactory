# Hermes Factory

Windows-native control plane for persistent teams of Hermes agents.

The project adapts the useful operating ideas from OpenRig without introducing a second agent harness or requiring tmux/Linux.

## Core mapping

| Factory concept | Hermes primitive |
|---|---|
| Seat | Hermes profile plus a stable role |
| Occupant | A replaceable Hermes session |
| Rig | A declarative team topology |
| Queue | Durable repository-backed work records |
| Handoff | Structured context passed to the next occupant |
| Refocus | Periodic restoration of project intent and acceptance criteria |
| Health | Progress, stall, context, and proof signals |

## MVP

1. Read `.hermes/rig.yaml`.
2. Validate profiles, working directories, roles, and edges.
3. Start and stop seats as native Windows Hermes processes.
4. Persist seat and work state in SQLite.
5. Support `up`, `status`, `send`, `stop`, and `handoff` commands.
6. Require proof artifacts before work can be marked complete.
7. Detect stalled or looping seats.

## Constraints

- Windows-native: no tmux, WSL, Docker, or POSIX shell dependency.
- Hermes remains the only agent runtime and source of session truth.
- Persistent identity belongs to a seat, not to one model context.
- Repository artifacts and SQLite are authoritative; agent chat is not.
- Human decisions cannot be silently delegated to another agent.

## Status

Project initialized. Architecture and first vertical slice are next.
