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


@dataclass(frozen=True, slots=True)
class RigSpec:
    version: int
    project: ProjectSpec
    board: BoardSpec
    seats: Mapping[str, SeatSpec]
    workflows: Mapping[str, WorkflowSpec]
    policies: PolicySpec

    def __post_init__(self) -> None:
        object.__setattr__(self, "seats", MappingProxyType(dict(self.seats)))
        object.__setattr__(self, "workflows", MappingProxyType(dict(self.workflows)))
