"""Citation resolution rate: of the claims a packet makes, how many point at something real.

An "evidence" assertion counts as resolved only if the chart really establishes its
criterion AND every citation exists in this patient's chart AND bears on that criterion.
Scored against Synthea ground truth, not an LLM judge: it is the thing a payer would
reject a packet over, and it must not depend on a model's opinion.

Scoring runs on real ids: rehydrate the packet first.
"""

from dataclasses import dataclass
from typing import Protocol

from app.graph.state import Assertion, Packet


class Truth(Protocol):
    criterion_ids: list[str]

    def resource_supports(
        self, patient_id: str, criterion_id: str, resource_type: str, resource_id: str
    ) -> tuple[bool, bool]: ...

    def established(self, patient_id: str, criterion_id: str) -> bool: ...


@dataclass(frozen=True)
class AssertionScore:
    criterion_id: str
    kind: str
    resolved: bool
    citations: int
    resolved_citations: int


@dataclass
class Score:
    assertions: list[AssertionScore]
    unaddressed: list[str]

    def _rate(self, num: int, den: int) -> float | None:
        return num / den if den else None  # no denominator is "not applicable", never 100%

    @property
    def evidence(self) -> list[AssertionScore]:
        return [a for a in self.assertions if a.kind == "evidence"]

    @property
    def gaps(self) -> list[AssertionScore]:
        return [a for a in self.assertions if a.kind == "gap"]

    @property
    def citation_resolution_rate(self) -> float | None:
        """The headline: resolved evidence assertions / evidence assertions."""
        return self._rate(sum(a.resolved for a in self.evidence), len(self.evidence))

    @property
    def citation_level_rate(self) -> float | None:
        """Finer grain: resolving citations / all citations made."""
        return self._rate(
            sum(a.resolved_citations for a in self.evidence), sum(a.citations for a in self.evidence)
        )

    @property
    def gap_accuracy(self) -> float | None:
        """Of the criteria the packet calls unsupported, how many truly are."""
        return self._rate(sum(a.resolved for a in self.gaps), len(self.gaps))


def score_packet(packet: Packet, patient_id: str, truth: Truth) -> Score:
    scores = [_score(a, patient_id, truth) for a in packet.assertions]
    mentioned = {a.criterion_id for a in packet.assertions}
    return Score(scores, [c for c in truth.criterion_ids if c not in mentioned])


def _score(a: Assertion, patient_id: str, truth: Truth) -> AssertionScore:
    if a.criterion_id not in truth.criterion_ids:
        return AssertionScore(a.criterion_id, a.kind, False, len(a.citations), 0)

    established = truth.established(patient_id, a.criterion_id)
    if a.kind == "gap":
        return AssertionScore(a.criterion_id, a.kind, not established and not a.citations, len(a.citations), 0)

    good = sum(
        all(truth.resource_supports(patient_id, a.criterion_id, c.resource_type, c.id))
        for c in a.citations
    )
    resolved = bool(a.citations) and established and good == len(a.citations)
    return AssertionScore(a.criterion_id, a.kind, resolved, len(a.citations), good)
