"""FHIR read tools exposed to the agent.

Read-only and patient-scoped. Nothing here scrubs PHI: this server stands in for the
EHR, and de-identification wraps its results on the agent side of the boundary.
"""

from app.mcp.models import (
    ConditionRecord,
    DocumentContent,
    DocumentMetadata,
    MedicationRequestRecord,
    ObservationRecord,
    PatientSummary,
)
from app.mcp.store import FhirStore


def _matching(records: list, code: str | None) -> list:
    return [r for r in records if code is None or r.code.code == code]


def list_patients(store: FhirStore) -> list[PatientSummary]:
    return store.patients()


def get_patient(store: FhirStore, patient_id: str) -> PatientSummary:
    return store.chart(patient_id).patient


def search_conditions(
    store: FhirStore, patient_id: str, code: str | None = None
) -> list[ConditionRecord]:
    return _matching(store.chart(patient_id).conditions, code)


def search_medication_requests(
    store: FhirStore, patient_id: str, code: str | None = None
) -> list[MedicationRequestRecord]:
    return _matching(store.chart(patient_id).medication_requests, code)


def search_observations(
    store: FhirStore, patient_id: str, code: str, limit: int = 50
) -> list[ObservationRecord]:
    if not code:
        raise ValueError("code is required: unfiltered observation dumps are not allowed")
    if limit < 1:
        raise ValueError("limit must be at least 1")
    return _matching(store.chart(patient_id).observations, code)[-limit:]


ResourceRecord = ConditionRecord | MedicationRequestRecord | ObservationRecord | DocumentMetadata


def get_resource(
    store: FhirStore, patient_id: str, resource_type: str, resource_id: str
) -> ResourceRecord:
    """Resolve one citation. Scoped to the patient: another patient's id does not resolve."""
    return store.chart(patient_id).find(resource_type, resource_id)


def list_documents(store: FhirStore, patient_id: str) -> list[DocumentMetadata]:
    return store.chart(patient_id).documents


def read_document(store: FhirStore, patient_id: str, document_id: str) -> DocumentContent:
    metadata, text = store.document_text(patient_id, document_id)
    return DocumentContent(**metadata.model_dump(), text=text)
