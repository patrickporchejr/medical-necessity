"""The only way the agent reaches the chart.

Wraps an MCP client session: ids in tool arguments are translated from placeholders to
real ids on the way in, and every result is de-identified on the way out, so nothing
downstream (graph nodes, prompts, traces) ever holds raw PHI.
"""

from typing import Any

from mcp import ClientSession

from app.phi.anonymize import Anonymizer
from app.phi.dates import shift_iso_dates

ID_ARGS = {"patient_id": "PATIENT", "document_id": "DOCUMENTREFERENCE"}
PASSTHROUGH_ARGS = {"code", "limit"}


class ToolCallError(RuntimeError):
    """A tool call failed; the message has already been scrubbed."""


class PhiGateway:
    def __init__(self, session: ClientSession, anonymizer: Anonymizer):
        self._session = session
        self.anonymizer = anonymizer
        self._learned: set[str] = set()

    async def adopt_patient(self, patient_id: str) -> str:
        """Register a real patient id (e.g. from a new case) and return its placeholder."""
        await self._learn_patient(patient_id)
        return self.anonymizer.pseudonym("PATIENT", patient_id)

    def shift_date(self, patient_id: str, iso_date: str) -> str:
        """Move a real date (e.g. the as-of date) into this patient's shifted timeline, so it
        can be compared with the shifted dates in their results."""
        vault = self.anonymizer.vault
        if vault.kind(patient_id) != "PATIENT":
            raise PermissionError("patient_id must be a placeholder issued by this gateway")
        return shift_iso_dates(iso_date, vault.date_offset(vault.original(patient_id)))

    async def list_tools(self):
        return await self._session.list_tools()

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        real_args = self._to_real(arguments)
        if "patient_id" in real_args:
            await self._learn_patient(real_args["patient_id"])
        result = await self._session.call_tool(name, real_args)
        if result.isError:
            raise ToolCallError(self.anonymizer.scrub_message(_error_text(result)))
        if result.structuredContent is None:
            raise ToolCallError(f"{name} returned unstructured content, which cannot be scrubbed")
        scrubbed = self.anonymizer.scrub_result(result.structuredContent)
        return scrubbed["result"] if set(scrubbed) == {"result"} else scrubbed

    def _to_real(self, arguments: dict[str, Any]) -> dict[str, Any]:
        vault = self.anonymizer.vault
        real: dict[str, Any] = {}
        for key, value in arguments.items():
            if key in ID_ARGS:
                original = vault.original(value) if isinstance(value, str) else None
                if original is None or vault.kind(value) != ID_ARGS[key]:
                    raise PermissionError(f"{key} must be a placeholder issued by this gateway")
                real[key] = original
            elif key in PASSTHROUGH_ARGS:
                real[key] = value
            else:
                raise PermissionError(f"Argument {key!r} has no PHI rule")
        return real

    async def _learn_patient(self, patient_id: str) -> None:
        """Fetch the patient once so their name is on the deny-list before any note is read."""
        if patient_id in self._learned:
            return
        result = await self._session.call_tool("get_patient", {"patient_id": patient_id})
        if result.isError or result.structuredContent is None:
            raise ToolCallError(self.anonymizer.scrub_message(_error_text(result)))
        self.anonymizer.scrub_result(result.structuredContent)  # registers names; output unused
        self._learned.add(patient_id)


def _error_text(result) -> str:
    return " ".join(getattr(block, "text", "") for block in result.content)
