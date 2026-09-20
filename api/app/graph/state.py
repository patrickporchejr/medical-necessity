"""Typed contracts shared by the graph nodes and the eval metric.

Inside the graph every id is a placeholder issued by the PHI gateway; real ids only
reappear when a packet is rehydrated for the reviewer.
"""

from pydantic import BaseModel, ConfigDict


class ResourceRef(BaseModel):
    """A citation: which chart resource a statement rests on."""

    model_config = ConfigDict(frozen=True)

    resource_type: str
    id: str


class EvidenceItem(BaseModel):
    ref: ResourceRef
    label: str
    date: str | None = None
    status: str | None = None
    value: float | str | bool | None = None


class NoteExcerpt(BaseModel):
    ref: ResourceRef
    date: str | None = None
    lines: list[str]


class CriterionEvidence(BaseModel):
    criterion_id: str
    description: str
    # What was searched, so "found nothing" is distinguishable from "never looked".
    queries: list[str]
    items: list[EvidenceItem] = []
    excerpts: list[NoteExcerpt] = []

    @property
    def found(self) -> bool:
        return bool(self.items or self.excerpts)


class Assertion(BaseModel):
    criterion_id: str
    text: str
    citations: list[ResourceRef]


class Packet(BaseModel):
    service: str
    assertions: list[Assertion]


class CaseState(BaseModel):
    patient_id: str  # placeholder, never the real id
    service: str | None = None
    evidence: list[CriterionEvidence] = []
    packet: Packet | None = None
