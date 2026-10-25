"""Cases and ground truth, read straight from the raw Synthea bundles.

This is deliberately a second, independent path to the chart: no gateway, no placeholders,
no shifted dates. It answers "what does the chart actually contain?" with real ids, which
is what a packet is scored against once its placeholders are rehydrated.
"""

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from app.config import settings
from app.graph.criteria import Criteria, load_criteria
from app.graph.nodes.extract import ONGOING, find_excerpt_lines
from app.graph.support import criterion_supports
from app.mcp.store import FhirStore, NotFound

CRITERIA_FILE = settings.criteria_dir / "adalimumab_ra.yaml"
HARD_DIR = settings.data_dir / "synthea" / "hard"  # G1.2's edited copies, built by data/hard/build.py
LABELS_DIR = Path(__file__).resolve().parent / "labels"  # G1.5's reference labels, never edited


def labels_as_of(labels_dir: Path | None = None) -> date:
    """The date the reference labels were judged at (both label files' `as_of`; cases.yaml says the same)."""
    import yaml

    dates = set()
    for name in ("held_out.yaml", "hard.yaml"):
        with open((labels_dir or LABELS_DIR) / name) as f:
            for line in f:  # the header only: `as_of` sits above the cases
                if line.startswith("as_of:"):
                    dates.add(str(yaml.safe_load(line)["as_of"]))
                    break
    if len(dates) != 1:
        raise SystemExit(f"the label files disagree on their as-of date: {sorted(dates)}")
    return date.fromisoformat(dates.pop())


def pinned_as_of(configured: date | None, labels_dir: Path | None = None) -> date:
    """The as-of date anything scored against the labels must use: theirs. Unset (AS_OF_DATE blank) means
    theirs; a different date is refused, since every answer that depends on a date would shift under it."""
    wanted = labels_as_of(labels_dir)
    if configured is not None and configured != wanted:
        raise SystemExit(f"AS_OF_DATE is {configured}, but the reference labels were judged as of {wanted}; "
                         f"unset AS_OF_DATE or set it to {wanted}")
    return wanted


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
        is_note = resource_type == "DocumentReference"
        text = chart.document_text.get(resource_id) if is_note else None
        code = None if is_note else record.code.code
        status = None if is_note else _status(record)
        return True, criterion_supports(self._by_id[criterion_id], resource_type, code, text, status)

    def established(self, patient_id: str, criterion_id: str) -> bool:
        """Does the whole chart, not just the first few notes, meet the criterion?"""
        criterion = self._by_id[criterion_id]
        chart = self.store.chart(patient_id)
        hits = [
            r for r in (*chart.conditions, *chart.medication_requests, *chart.observations)
            if criterion_supports(criterion, r.resource_type, r.code.code, None, _status(r))
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


def _status(record) -> str | None:
    # A Condition's clinical status, or a request's status; the same rule verify applies.
    return getattr(record, "clinical_status", None) or getattr(record, "status", None)


def _start(record) -> str:
    # Each record type names its date differently (onset_date, authored_on, effective_date).
    return next(getattr(record, k) for k in ("onset_date", "authored_on", "effective_date") if hasattr(record, k))


def open_ground_truth(as_of: date, fhir_dir: Path | None = None) -> GroundTruth:
    """The rule's answers over a bundle directory: the cohort by default, or HARD_DIR for the hard split."""
    return GroundTruth(FhirStore(fhir_dir or settings.fhir_dir), load_criteria(CRITERIA_FILE), as_of)
