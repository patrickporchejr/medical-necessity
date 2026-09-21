"""What it means for chart evidence to support a criterion.

Shared by `verify` (at runtime, over the gateway) and the eval's ground truth (offline,
over the raw bundles), so the two cannot drift apart on the definition.
"""

from app.graph.criteria import Criterion, EvidenceSpec
from app.graph.nodes.extract import find_excerpt_lines
from app.graph.state import CriterionEvidence


def record_status(record: dict) -> str | None:
    """A Condition's clinical status, or a request's status."""
    return record.get("clinical_status") or record.get("status")


def spec_supports(
    spec: EvidenceSpec, resource_type: str, code: str | None, note_text: str | None,
    status: str | None = None,
) -> bool:
    if spec.resource != resource_type:
        return False
    if spec.status is not None and status != spec.status:
        return False
    if spec.resource == "DocumentReference":
        return bool(note_text and find_excerpt_lines(note_text, spec.keywords))
    return code is not None and code == spec.code.code


def criterion_supports(
    criterion: Criterion, resource_type: str, code: str | None, note_text: str | None,
    status: str | None = None,
) -> bool:
    return any(spec_supports(s, resource_type, code, note_text, status) for s in criterion.evidence)


def established(evidence: CriterionEvidence) -> bool:
    """The chart meets the criterion: matching evidence exists and, where the criterion has
    a minimum duration, that duration is shown (an unknown duration does not count)."""
    return evidence.found and evidence.duration_status in (None, "met")
