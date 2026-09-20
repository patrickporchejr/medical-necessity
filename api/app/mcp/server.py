import re
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from app.config import settings
from app.mcp import tools
from app.mcp.models import (
    ConditionRecord,
    DocumentContent,
    DocumentMetadata,
    MedicationRequestRecord,
    ObservationRecord,
    PatientSummary,
)
from app.mcp.store import FhirStore

CRITERIA_NAME = re.compile(r"^[a-z0-9_]+$")


def build_server(fhir_dir: Path, criteria_dir: Path, **fastmcp_kwargs) -> FastMCP:
    store = FhirStore(fhir_dir)
    mcp = FastMCP(
        "medical-necessity-fhir",
        instructions=(
            "Read-only FHIR access to a synthetic patient chart. Every record has an `id` and "
            "`resource_type`; cite those when asserting anything drawn from the chart. Filter by "
            "`code` to fetch only the evidence a criterion needs."
        ),
        **fastmcp_kwargs,
    )

    @mcp.tool()
    def list_patients() -> list[PatientSummary]:
        """List every patient in the cohort (id, name, birth date, gender)."""
        return tools.list_patients(store)

    @mcp.tool()
    def get_patient(patient_id: str) -> PatientSummary:
        """One patient's id, name, birth date and gender."""
        return tools.get_patient(store, patient_id)

    @mcp.tool()
    def search_conditions(patient_id: str, code: str | None = None) -> list[ConditionRecord]:
        """Conditions for a patient, oldest first. `code` filters on the exact code value
        (e.g. SNOMED "69896004" for rheumatoid arthritis); omit it for all conditions."""
        return tools.search_conditions(store, patient_id, code)

    @mcp.tool()
    def search_medication_requests(
        patient_id: str, code: str | None = None
    ) -> list[MedicationRequestRecord]:
        """Medication orders for a patient, oldest first. `code` filters on the exact RxNorm
        code (e.g. "105585" for methotrexate 2.5 MG oral tablet); omit it for all orders."""
        return tools.search_medication_requests(store, patient_id, code)

    @mcp.tool()
    def search_observations(
        patient_id: str, code: str, limit: int = 50
    ) -> list[ObservationRecord]:
        """Observations (labs, vitals) for a patient matching an exact LOINC `code`, oldest
        first. `code` is required. Returns the most recent `limit` matches."""
        return tools.search_observations(store, patient_id, code, limit)

    @mcp.tool()
    def list_documents(patient_id: str) -> list[DocumentMetadata]:
        """Clinical note metadata for a patient, oldest first: id, type, date, author.
        Use `read_document` to fetch a note's text."""
        return tools.list_documents(store, patient_id)

    @mcp.tool()
    def read_document(patient_id: str, document_id: str) -> DocumentContent:
        """Full text of one clinical note."""
        return tools.read_document(store, patient_id, document_id)

    @mcp.resource("criteria://{name}", mime_type="application/yaml")
    def payer_criteria(name: str) -> str:
        """Payer criteria for a service line, as YAML (e.g. criteria://adalimumab_ra)."""
        if not CRITERIA_NAME.match(name):
            raise ValueError(f"Invalid criteria name: {name}")
        path = criteria_dir / f"{name}.yaml"
        if not path.is_file():
            raise ValueError(f"Unknown criteria: {name}")
        return path.read_text()

    return mcp


def main() -> None:
    build_server(
        settings.fhir_dir,
        settings.criteria_dir,
        host=settings.mcp_host,
        port=settings.mcp_port,
    ).run(transport="streamable-http")


if __name__ == "__main__":
    main()
