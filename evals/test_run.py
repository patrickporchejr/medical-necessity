import json
import re
from dataclasses import asdict
from datetime import date

import pytest

from app.config import settings
from app.graph.nodes.assemble import PacketDraft
from app.graph.state import Assertion, ResourceRef
from app.llm.client import LLMRefusal, LLMResult
from dataset import open_ground_truth
from run import connect, format_result, resolve_patient, run_case, write_results

AS_OF = date(2026, 9, 20)
needs_cohort = pytest.mark.skipif(
    not any(settings.fhir_dir.glob("*.json")), reason="Synthea cohort not generated"
)


def faithful_from_prompt(prompt: str) -> PacketDraft:
    """A model that does the job right, working only from what the prompt says."""
    assertions = []
    for block in re.split(r"\n(?=Criterion )", prompt)[1:]:
        cid = re.match(r"Criterion (\w+):", block).group(1)
        lines = [ln for ln in block.splitlines() if ln.strip().startswith("- ")]
        required = re.search(r"Required status: (\w+)", block)
        if required:  # only records that carry the required status count
            lines = [ln for ln in lines if f"status {required.group(1)}" in ln]
        refs = re.findall(r"- (\w+) (<[A-Z]+_\d+>)", "\n".join(lines))
        status = re.search(r"Duration status: (\w+)", block)
        if ("No matching records" in block or (status and status.group(1) != "met")
                or (required and not refs)):
            assertions.append(Assertion(criterion_id=cid, kind="gap", text="not established by the chart"))
        else:
            cites = [ResourceRef(resource_type=t, id=i) for t, i in refs]
            assertions.append(Assertion(criterion_id=cid, text="established by the chart", citations=cites))
    return PacketDraft(assertions=assertions)


def overclaiming_from_prompt(prompt: str) -> PacketDraft:
    """The failure modes we care about: claims the completed order meets the duration, and
    invents evidence for TB screening."""
    out = []
    for a in faithful_from_prompt(prompt).assertions:
        if a.criterion_id == "dmard_trial":
            ref = re.search(r"- (MedicationRequest) (<[A-Z]+_\d+>)", prompt)
            a = Assertion(criterion_id="dmard_trial", text="3 months of methotrexate",
                          citations=[ResourceRef(resource_type=ref.group(1), id=ref.group(2))])
        if a.criterion_id == "tb_screening":
            a = Assertion(criterion_id="tb_screening", text="TB screening negative",
                          citations=[ResourceRef(resource_type="Observation", id="<OBSERVATION_99>")])
        out.append(a)
    return PacketDraft(assertions=out)


def naive_ra_from_prompt(prompt: str) -> PacketDraft:
    """Ignores the status: claims the RA diagnosis as evidence whenever an RA condition is listed."""
    out = []
    for a in faithful_from_prompt(prompt).assertions:
        if a.criterion_id == "ra_diagnosis":
            ref = re.search(r"- (Condition) (<[A-Z]+_\d+>)", prompt)
            a = Assertion(criterion_id="ra_diagnosis", text="confirmed RA",
                          citations=[ResourceRef(resource_type=ref.group(1), id=ref.group(2))])
        out.append(a)
    return PacketDraft(assertions=out)


class Scripted:
    provider = "fake"

    def __init__(self, respond):
        self.respond = respond

    async def generate(self, system, user, schema):
        return LLMResult(parsed=self.respond(user), provider="fake", model="scripted",
                         input_tokens=100, output_tokens=50)


class Refusing:
    provider = "fake"

    async def generate(self, system, user, schema):
        raise LLMRefusal("declined (category: test)")


@pytest.fixture(scope="module")
def truth():
    return open_ground_truth(AS_OF)


async def run(truth, name, respond):
    async with connect(None) as session:
        return await run_case(session, Scripted(respond), resolve_patient(truth, name), truth, AS_OF)


# --- the runner on the real cohort, with a scripted model ---------------------------------------

@needs_cohort
@pytest.mark.anyio
async def test_a_faithful_model_on_the_completed_order_patient_scores_perfectly(truth):
    result, _ = await run(truth, "Loyd638", faithful_from_prompt)
    assert result.ok, result.error
    kinds = {a["criterion_id"]: a["kind"] for a in result.assertions}
    assert kinds == {"ra_diagnosis": "evidence", "dmard_trial": "gap", "active_disease": "evidence",
                     "tb_screening": "gap", "hepatitis_b_screening": "gap"}
    assert (result.citation_resolution_rate, result.gap_accuracy) == (1.0, 1.0)
    assert result.verify_agrees_with_truth and all(a["verify_supported"] for a in result.assertions)
    assert result.model == "fake:scripted" and (result.input_tokens, result.output_tokens) == (100, 50)
    assert set(result.seconds) == {"extract", "assemble", "verify"}


@needs_cohort
@pytest.mark.anyio
async def test_a_faithful_model_on_a_met_patient_claims_the_methotrexate_trial(truth):
    result, _ = await run(truth, "Aaron697", faithful_from_prompt)
    kinds = {a["criterion_id"]: a["kind"] for a in result.assertions}
    assert kinds["dmard_trial"] == "evidence" and result.citation_resolution_rate == 1.0


@needs_cohort
@pytest.mark.anyio
async def test_an_overclaiming_model_is_caught_and_verify_agrees_with_ground_truth(truth):
    result, real = await run(truth, "Loyd638", overclaiming_from_prompt)
    assert result.ok and result.verify_agrees_with_truth
    flagged = {a["criterion_id"] for a in result.assertions if not a["verify_supported"]}
    assert flagged == {"dmard_trial", "tb_screening"}
    assert result.citation_resolution_rate == pytest.approx(2 / 4)  # ra + active resolve; dmard + tb do not
    text = format_result(result, real)
    assert "FLAG" in text and "duration undetermined" in text and "not found in this patient" in text


@needs_cohort
@pytest.mark.anyio
async def test_a_refusing_model_is_a_recorded_failure_not_a_crash(truth):
    async with connect(None) as session:
        result, real = await run_case(session, Refusing(), resolve_patient(truth, "Loyd638"), truth, AS_OF)
    assert not result.ok and "LLMRefusal" in result.error and real is None
    assert "FAILED" in format_result(result, real)


@needs_cohort
@pytest.mark.anyio
async def test_the_saved_result_holds_nothing_identifying(truth):
    result, _ = await run(truth, "Loyd638", faithful_from_prompt)
    patient_id = result.patient_id
    saved = asdict(result) | {"patient_id": "<the case key, synthetic>"}
    blob = json.dumps(saved)
    chart = truth.store.chart(patient_id)
    real_values = {*chart.patient.name.split(), chart.patient.birth_date, chart.patient.id,
                   *(c.id for c in chart.conditions), *(m.id for m in chart.medication_requests)}
    assert not [v for v in real_values if v and re.search(rf"(?<![\w-]){re.escape(v)}(?![\w-])", blob)]
    assert "<CONDITION_" in blob


# --- resolved RA: the diagnosis exists but is not active ----------------------------------------

@needs_cohort
def test_ground_truth_requires_an_active_diagnosis(truth):
    denis, aaron = resolve_patient(truth, "Denis399"), resolve_patient(truth, "Aaron697")
    assert not truth.established(denis, "ra_diagnosis") and truth.established(aaron, "ra_diagnosis")
    condition = next(c for c in truth.store.chart(denis).conditions if c.code.code == "69896004")
    assert condition.clinical_status == "resolved"
    assert truth.resource_supports(denis, "ra_diagnosis", "Condition", condition.id) == (True, False)


@needs_cohort
@pytest.mark.anyio
async def test_a_faithful_model_writes_a_gap_for_a_resolved_diagnosis(truth):
    result, _ = await run(truth, "Denis399", faithful_from_prompt)
    kinds = {a["criterion_id"]: a["kind"] for a in result.assertions}
    assert kinds["ra_diagnosis"] == "gap" and set(kinds.values()) == {"gap"}
    assert result.gap_accuracy == 1.0 and result.verify_agrees_with_truth


@needs_cohort
@pytest.mark.anyio
async def test_claiming_a_resolved_diagnosis_is_caught_and_verify_agrees(truth):
    from test_run import naive_ra_from_prompt as naive

    result, real = await run(truth, "Denis399", naive)
    ra = next(a for a in result.assertions if a["criterion_id"] == "ra_diagnosis")
    assert ra["kind"] == "evidence" and not ra["verify_supported"] and not ra["truth_resolved"]
    assert result.verify_agrees_with_truth
    assert "does not establish" in " ".join(ra["reasons"])


# --- plumbing -----------------------------------------------------------------------------------

@needs_cohort
def test_patients_resolve_by_id_or_a_unique_name_fragment(truth):
    loyd = resolve_patient(truth, "loyd638")
    assert resolve_patient(truth, loyd) == loyd
    with pytest.raises(SystemExit, match="exactly one"):
        resolve_patient(truth, "Adams676")  # two patients share that family name
    with pytest.raises(SystemExit, match="nothing"):
        resolve_patient(truth, "no-such-patient")


def test_results_are_written_as_json_with_their_run_metadata(tmp_path):
    from run import CaseResult

    path = write_results([CaseResult(patient_id="abcd1234-x", provider="fake", as_of="2026-09-20")],
                         {"mcp": "in-process"}, tmp_path / "runs")
    data = json.loads(path.read_text())
    assert path.name.endswith("_abcd1234.json") and data["meta"] == {"mcp": "in-process"}
    assert data["results"][0]["provider"] == "fake"
