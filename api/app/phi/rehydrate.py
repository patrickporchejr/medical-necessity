"""Restore real values in text bound for the reviewer."""

from typing import Any

from app.phi.dates import shift_iso_dates
from app.phi.vault import PLACEHOLDER, Vault


class AmbiguousPatient(ValueError):
    pass


def rehydrate(value: Any, vault: Vault, patient_id: str | None = None) -> Any:
    """Replace placeholders with originals and undo the date shift, recursively.

    `patient_id` (real id or placeholder) says whose dates to un-shift; it can be omitted
    when the vault has seen exactly one patient. Unknown placeholders are left as-is.
    """
    delta = _delta(vault, patient_id)
    return _walk(value, vault, delta)


def _delta(vault: Vault, patient_id: str | None):
    if patient_id is not None:
        patient_id = vault.original(patient_id) or patient_id
        return -vault.date_offset(patient_id)
    patients = vault.patients_with_offsets()
    if len(patients) > 1:
        raise AmbiguousPatient("Vault holds several patients; pass patient_id to rehydrate")
    return -vault.date_offset(patients[0]) if patients else None


def _walk(value: Any, vault: Vault, delta) -> Any:
    if isinstance(value, str):
        if delta is not None:
            value = shift_iso_dates(value, delta)
        return PLACEHOLDER.sub(lambda m: vault.original(m.group(0)) or m.group(0), value)
    if isinstance(value, list):
        return [_walk(item, vault, delta) for item in value]
    if isinstance(value, dict):
        return {key: _walk(item, vault, delta) for key, item in value.items()}
    return value
