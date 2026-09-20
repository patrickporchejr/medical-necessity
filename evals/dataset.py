"""Cases and ground truth, read straight from the raw Synthea bundles.

This is deliberately a second, independent path to the chart: no gateway, no placeholders,
no shifted dates. It answers "what does the chart actually contain?" with real ids, which
is what a packet is scored against once its placeholders are rehydrated.
"""

from dataclasses import dataclass
from datetime import date

from app.config import settings
from app.graph.criteria import Criteria, load_criteria
from app.graph.nodes.extract import ONGOING, find_excerpt_lines
from app.graph.support import criterion_supports
from app.mcp.store import FhirStore, NotFound

CRITERIA_FILE = settings.criteria_dir / "adalimumab_ra.yaml"


@dataclass(frozen=True)
class Case:
    patient_id: str
    name: str


class GroundTruth:
    def __init__(self, store: FhirStore, criteria: Criteria, as_of: date):
        self.store, self.criteria, self.as_of = store, criteria, as_of
        self._by_id = {c.id: c for c in criteria.criteria}

    @property
    def criterion_ids(self) -> list[str]:
        return list(self._by_id)

    def cases(self) -> list[Case]:
        return [Case(p.id, p.name) for p in self.store.patients()]

    def resource_supports(
        self, patient_id: str, criterion_id: str, resource_type: str, resource_id: str
    ) -> tuple[bool, bool]:
        """(exists in this patient's chart, bears on the criterion)."""
        try:
            chart = self.store.chart(patient_id)
            record = chart.find(resource_type, resource_id)
        except NotFound:
            return False, False
        if criterion_id not in self._by_id:
            return True, False
        text = chart.document_text.get(resource_id) if resource_type == "DocumentReference" else None
        code = None if resource_type == "DocumentReference" else record.code.code
        return True, criterion_supports(self._by_id[criterion_id], resource_type, code, text)

    def established(self, patient_id: str, criterion_id: str) -> bool:
        """Does the whole chart, not just the first few notes, meet the criterion?"""
        criterion = self._by_id[criterion_id]
        chart = self.store.chart(patient_id)
        hits = [
            r for r in (*chart.conditions, *chart.medication_requests, *chart.observations)
            if criterion_supports(criterion, r.resource_type, r.code.code, None)
        ]
        note_hit = any(
            criterion_supports(criterion, "DocumentReference", None, text)
            for text in chart.document_text.values()
        )
        if criterion.min_duration_days is None:
            return bool(hits) or note_hit
        return any(
            getattr(r, "status", None) in ONGOING
            and (self.as_of - date.fromisoformat(_start(r)[:10])).days >= criterion.min_duration_days
            for r in hits
        )


def _start(record) -> str:
    # Each record type names its date differently (onset_date, authored_on, effective_date).
    return next(getattr(record, k) for k in ("onset_date", "authored_on", "effective_date") if hasattr(record, k))


def open_ground_truth(as_of: date) -> GroundTruth:
    return GroundTruth(FhirStore(settings.fhir_dir), load_criteria(CRITERIA_FILE), as_of)
