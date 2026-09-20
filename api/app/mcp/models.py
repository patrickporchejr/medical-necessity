"""Typed results returned by the FHIR read tools.

Every record carries `id` and `resource_type`: together they are the citation the
agent attaches to an assertion and that `verify` resolves back to the chart.
"""

from typing import Literal

from pydantic import BaseModel


class Code(BaseModel):
    system: str | None = None
    code: str
    display: str | None = None


class PatientSummary(BaseModel):
    id: str
    resource_type: Literal["Patient"] = "Patient"
    name: str
    birth_date: str | None = None
    gender: str | None = None


class ConditionRecord(BaseModel):
    id: str
    resource_type: Literal["Condition"] = "Condition"
    patient_id: str
    code: Code
    clinical_status: str | None = None
    verification_status: str | None = None
    onset_date: str | None = None
    recorded_date: str | None = None


class MedicationRequestRecord(BaseModel):
    id: str
    resource_type: Literal["MedicationRequest"] = "MedicationRequest"
    patient_id: str
    code: Code
    status: str | None = None
    authored_on: str | None = None
    dosage: str | None = None
    reason: str | None = None


class ObservationRecord(BaseModel):
    id: str
    resource_type: Literal["Observation"] = "Observation"
    patient_id: str
    code: Code
    status: str | None = None
    effective_date: str | None = None
    value: float | str | bool | None = None
    unit: str | None = None


class DocumentMetadata(BaseModel):
    id: str
    resource_type: Literal["DocumentReference"] = "DocumentReference"
    patient_id: str
    type: Code | None = None
    date: str | None = None
    author: str | None = None
    status: str | None = None


class DocumentContent(DocumentMetadata):
    text: str
