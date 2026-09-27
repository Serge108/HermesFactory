from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True, slots=True)
class ProjectSpec:
    slug: str
    name: str
    cwd: Path


@dataclass(frozen=True, slots=True)
class BoardSpec:
    slug: str
    name: str


@dataclass(frozen=True, slots=True)
class SeatSpec:
    profile: str
    role: str
    skills: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WorkflowSpec:
    implementation_seat: str
    review_seat: str
    acceptance_seat: str


@dataclass(frozen=True, slots=True)
class PolicySpec:
    require_proof: bool
    human_acceptance: bool
    max_retries: int
    max_runtime: str
    completion_contract: str
    decision_authority: str
    git_commit_policy: str
    fixture_recovery: bool


@dataclass(frozen=True, slots=True)
class TaskSubscriptionSpec:
    platform: str
    chat_id: str
    user_id: str
    chat_type: str
    notifier_profile: str
    delivery_mode: str


@dataclass(frozen=True, slots=True)
class NotificationSpec:
    task_subscriptions: tuple[TaskSubscriptionSpec, ...]


@dataclass(frozen=True, slots=True)
class ProofSpec:
    commands: tuple[str, ...]
    artifacts: tuple[str, ...]
    changed_files_required: bool


@dataclass(frozen=True, slots=True)
class SliceSpec:
    id: str
    title: str
    goal: str
    depends_on: tuple[str, ...]
    acceptance: tuple[str, ...]
    proof: ProofSpec | None


@dataclass(frozen=True, slots=True)
class MissionSpec:
    version: int
    id: str
    title: str
    intent: str
    workflow: str
    human_acceptance: bool
    acceptance: tuple[str, ...]
    slices: tuple[SliceSpec, ...]


@dataclass(frozen=True, slots=True)
class RigSpec:
    version: int
    project: ProjectSpec
    board: BoardSpec
    seats: Mapping[str, SeatSpec]
    workflows: Mapping[str, WorkflowSpec]
    notifications: NotificationSpec
    policies: PolicySpec

    def __post_init__(self) -> None:
        object.__setattr__(self, "seats", MappingProxyType(dict(self.seats)))
        object.__setattr__(self, "workflows", MappingProxyType(dict(self.workflows)))
