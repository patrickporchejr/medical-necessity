"""Payer criteria, loaded from YAML into typed models."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, model_validator


class CodeSpec(BaseModel):
    system: str
    code: str


class EvidenceSpec(BaseModel):
    resource: Literal["Condition", "MedicationRequest", "Observation", "DocumentReference"]
    code: CodeSpec | None = None
    keywords: list[str] = []
    # The record must carry this status: a Condition's clinical status, a request's status.
    status: str | None = None

    @model_validator(mode="after")
    def _has_what_it_searches_for(self):
        if self.resource == "DocumentReference":
            if not self.keywords:
                raise ValueError("DocumentReference evidence needs keywords")
            if self.status:
                raise ValueError("DocumentReference evidence has no status to require")
        elif self.code is None:
            raise ValueError(f"{self.resource} evidence needs a code")
        return self


class Criterion(BaseModel):
    id: str
    description: str
    min_duration_days: int | None = None
    evidence: list[EvidenceSpec]


class Criteria(BaseModel):
    service: str
    criteria: list[Criterion]


def load_criteria(path: Path) -> Criteria:
    return Criteria.model_validate(yaml.safe_load(path.read_text()))
