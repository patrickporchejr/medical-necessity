"""Typed contracts shared by the graph nodes and the eval metric.

Inside the graph every id is a placeholder issued by the PHI gateway; real ids only
reappear when a packet is rehydrated for the reviewer.
"""

from typing import Literal

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
    # Days the item has been in effect at the as-of date. Only known for an ongoing order:
    # the chart records a start date and a status, never a stop date.
    days_in_effect: int | None = None
    # False when the record matches the criterion's code but not its required status.
    qualifies: bool = True


class NoteExcerpt(BaseModel):
    ref: ResourceRef
    date: str | None = None
    lines: list[str]


class CriterionEvidence(BaseModel):
    criterion_id: str
    description: str
    # What was searched, so "found nothing" is distinguishable from "never looked".
    queries: list[str]
    min_duration_days: int | None = None
    required_status: str | None = None
    items: list[EvidenceItem] = []
    excerpts: list[NoteExcerpt] = []

    @property
    def found(self) -> bool:
        """Something in the chart bears on the criterion and meets its status requirement."""
        return any(i.qualifies for i in self.items) or bool(self.excerpts)

    @property
    def duration_status(self) -> Literal["met", "not_met", "undetermined"] | None:
        """None when the criterion has no duration requirement. "undetermined" means an
        item ended (or its length is otherwise unknown) so the chart cannot show either way."""
        if self.min_duration_days is None:
            return None
        if any(i.days_in_effect is not None and i.days_in_effect >= self.min_duration_days
               for i in self.items):
            return "met"
        if any(i.days_in_effect is None for i in self.items):
            return "undetermined"
        return "not_met"


class Assertion(BaseModel):
    """A statement about one criterion.

    kind "evidence" says the chart meets it and must cite what shows that. kind "gap" says
    the chart does not establish it, and cites nothing. Without "gap" the only way to state
    an unsupported criterion would be to fabricate evidence for it.
    """

    criterion_id: str
    kind: Literal["evidence", "gap"] = "evidence"
    text: str
    citations: list[ResourceRef] = []


class Packet(BaseModel):
    service: str
    assertions: list[Assertion]


class CitationCheck(BaseModel):
    ref: ResourceRef
    exists: bool  # resolves to a resource in this patient's chart
    supports: bool  # ...and that resource bears on the assertion's criterion
    reason: str | None = None


class VerifiedAssertion(BaseModel):
    assertion: Assertion
    supported: bool
    checks: list[CitationCheck] = []
    reasons: list[str] = []


class Verification(BaseModel):
    """Every assertion with its verdict. Nothing is dropped: unsupported ones are flagged."""

    assertions: list[VerifiedAssertion]
    unaddressed: list[str]  # criteria the packet never mentions

    @property
    def flagged(self) -> list[VerifiedAssertion]:
        return [a for a in self.assertions if not a.supported]


class CaseState(BaseModel):
    patient_id: str  # placeholder, never the real id
    service: str | None = None
    as_of: str | None = None  # the as-of date on this patient's shifted timeline
    evidence: list[CriterionEvidence] = []
    packet: Packet | None = None
    assembled_by: str | None = None  # provider:model that drafted the packet
    verification: Verification | None = None
