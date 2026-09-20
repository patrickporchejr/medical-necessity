"""Read-only access to the Synthea FHIR R4 bundles.

One bundle per patient, ~10 MB each, so the whole cohort is never held in memory:
a bundle is parsed on first touch, reduced to the four resource types the tools
expose, and kept in a small LRU. `list_patients` reads every bundle once and
caches only the summaries.
"""

import base64
import json
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.mcp.models import (
    Code,
    ConditionRecord,
    DocumentMetadata,
    MedicationRequestRecord,
    ObservationRecord,
    PatientSummary,
)

# Synthea writes these alongside the patient bundles; they are not charts.
NON_PATIENT_PREFIXES = ("hospitalInformation", "practitionerInformation")
CACHE_SIZE = 8


class NotFound(LookupError):
    pass


@dataclass
class Chart:
    patient: PatientSummary
    conditions: list[ConditionRecord] = field(default_factory=list)
    medication_requests: list[MedicationRequestRecord] = field(default_factory=list)
    observations: list[ObservationRecord] = field(default_factory=list)
    documents: list[DocumentMetadata] = field(default_factory=list)
    document_text: dict[str, str] = field(default_factory=dict)
    _index: dict[tuple[str, str], Any] | None = field(default=None, repr=False)

    def find(self, resource_type: str, resource_id: str) -> Any:
        if self._index is None:
            records = [*self.conditions, *self.medication_requests, *self.observations, *self.documents]
            self._index = {(r.resource_type, r.id): r for r in records}
        record = self._index.get((resource_type, resource_id))
        if record is None:
            raise NotFound(f"No {resource_type} {resource_id} for patient {self.patient.id}")
        return record


class FhirStore:
    def __init__(self, fhir_dir: Path):
        self._paths: dict[str, Path] = {}
        for path in sorted(fhir_dir.glob("*.json")):
            if path.name.startswith(NON_PATIENT_PREFIXES):
                continue
            # Synthea names bundles <given>_<family>_<patient uuid>.json
            self._paths[path.stem.rsplit("_", 1)[-1]] = path
        self._charts: OrderedDict[str, Chart] = OrderedDict()
        self._summaries: dict[str, PatientSummary] = {}

    def patient_ids(self) -> list[str]:
        return list(self._paths)

    def patients(self) -> list[PatientSummary]:
        for patient_id in self._paths:
            if patient_id not in self._summaries:
                self._summaries[patient_id] = self.chart(patient_id).patient
        return sorted(self._summaries.values(), key=lambda p: p.name)

    def chart(self, patient_id: str) -> Chart:
        if patient_id in self._charts:
            self._charts.move_to_end(patient_id)
            return self._charts[patient_id]
        path = self._paths.get(patient_id)
        if path is None:
            raise NotFound(f"Unknown patient_id: {patient_id}")
        chart = _parse_bundle(json.loads(path.read_text()), patient_id)
        self._summaries[patient_id] = chart.patient
        self._charts[patient_id] = chart
        if len(self._charts) > CACHE_SIZE:
            self._charts.popitem(last=False)
        return chart

    def document_text(self, patient_id: str, document_id: str) -> tuple[DocumentMetadata, str]:
        chart = self.chart(patient_id)
        for doc in chart.documents:
            if doc.id == document_id:
                return doc, chart.document_text[document_id]
        raise NotFound(f"Unknown document_id for patient {patient_id}: {document_id}")


def _codings(codeable: dict[str, Any] | None) -> list[dict[str, Any]]:
    return (codeable or {}).get("coding", [])


def _code(codeable: dict[str, Any] | None, prefer: str | None = None) -> Code | None:
    """First coding of a CodeableConcept, preferring `prefer` (a system URI) if present."""
    codings = _codings(codeable)
    if not codings:
        return None
    chosen = next((c for c in codings if c.get("system") == prefer), codings[0])
    return Code(
        system=chosen.get("system"),
        code=chosen["code"],
        display=chosen.get("display") or (codeable or {}).get("text"),
    )


def _status(codeable: dict[str, Any] | None) -> str | None:
    codings = _codings(codeable)
    return codings[0].get("code") if codings else None


def _patient_name(resource: dict[str, Any]) -> str:
    names = resource.get("name") or []
    official = next((n for n in names if n.get("use") == "official"), names[0] if names else {})
    return " ".join([*official.get("given", []), official.get("family", "")]).strip()


def _dosage(resource: dict[str, Any]) -> str | None:
    instructions = resource.get("dosageInstruction") or []
    return instructions[0].get("text") if instructions else None


def _observation_value(resource: dict[str, Any]) -> tuple[float | str | bool | None, str | None]:
    if "valueQuantity" in resource:
        quantity = resource["valueQuantity"]
        return quantity.get("value"), quantity.get("unit")
    if "valueCodeableConcept" in resource:
        concept = resource["valueCodeableConcept"]
        coded = _code(concept)
        return concept.get("text") or (coded.display if coded else None), None
    for key in ("valueString", "valueBoolean", "valueInteger"):
        if key in resource:
            return resource[key], None
    return None, None


def _parse_bundle(bundle: dict[str, Any], patient_id: str) -> Chart:
    resources = [entry["resource"] for entry in bundle["entry"]]
    patient = next((r for r in resources if r["resourceType"] == "Patient"), None)
    if patient is None or patient["id"] != patient_id:
        raise NotFound(f"Bundle for {patient_id} has no matching Patient resource")

    chart = Chart(
        patient=PatientSummary(
            id=patient_id,
            name=_patient_name(patient),
            birth_date=patient.get("birthDate"),
            gender=patient.get("gender"),
        )
    )
    for r in resources:
        kind = r["resourceType"]
        if kind == "Condition" and (code := _code(r.get("code"))):
            chart.conditions.append(
                ConditionRecord(
                    id=r["id"],
                    patient_id=patient_id,
                    code=code,
                    clinical_status=_status(r.get("clinicalStatus")),
                    verification_status=_status(r.get("verificationStatus")),
                    onset_date=r.get("onsetDateTime"),
                    recorded_date=r.get("recordedDate"),
                )
            )
        elif kind == "MedicationRequest" and (code := _code(r.get("medicationCodeableConcept"))):
            reasons = r.get("reasonReference") or []
            chart.medication_requests.append(
                MedicationRequestRecord(
                    id=r["id"],
                    patient_id=patient_id,
                    code=code,
                    status=r.get("status"),
                    authored_on=r.get("authoredOn"),
                    dosage=_dosage(r),
                    reason=reasons[0].get("display") if reasons else None,
                )
            )
        elif kind == "Observation" and (code := _code(r.get("code"))):
            value, unit = _observation_value(r)
            chart.observations.append(
                ObservationRecord(
                    id=r["id"],
                    patient_id=patient_id,
                    code=code,
                    status=r.get("status"),
                    effective_date=r.get("effectiveDateTime"),
                    value=value,
                    unit=unit,
                )
            )
        elif kind == "DocumentReference":
            attachment = next(
                (c["attachment"] for c in r.get("content", []) if c["attachment"].get("data")),
                None,
            )
            if attachment is None:
                continue
            authors = r.get("author") or []
            chart.documents.append(
                DocumentMetadata(
                    id=r["id"],
                    patient_id=patient_id,
                    type=_code(r.get("type"), prefer="http://loinc.org"),
                    date=r.get("date"),
                    author=authors[0].get("display") if authors else None,
                    status=r.get("status"),
                )
            )
            chart.document_text[r["id"]] = base64.b64decode(attachment["data"]).decode("utf-8")

    # Oldest first: criteria such as "at least 3 months of methotrexate" read forward in time.
    chart.conditions.sort(key=lambda x: x.onset_date or "")
    chart.medication_requests.sort(key=lambda x: x.authored_on or "")
    chart.observations.sort(key=lambda x: x.effective_date or "")
    chart.documents.sort(key=lambda x: x.date or "")
    return chart
