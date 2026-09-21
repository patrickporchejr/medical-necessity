import json
import re
from datetime import date

import pytest

from dataset import open_ground_truth
from experiment import (
    PatientInput,
    build_dataset,
    client_for,
    make_task,
    select_patients,
    stratum_of,
)
from run import connect, resolve_patient
from test_run import Refusing, Scripted, faithful_from_prompt, overclaiming_from_prompt
from app.config import settings

AS_OF = date(2026, 9, 20)
needs_cohort = pytest.mark.skipif(
    not any(settings.fhir_dir.glob("*.json")), reason="Synthea cohort not generated"
)


@pytest.fixture(scope="module")
def truth():
    return open_ground_truth(AS_OF)


# --- the dataset --------------------------------------------------------------------------------

@needs_cohort
def test_the_dataset_covers_every_situation_with_a_small_and_a_large_chart(truth):
    selected = select_patients(truth)
    strata = {s for _, s in selected}
    # every situation that occurs in the cohort is covered, including a resolved diagnosis
    assert strata == {stratum_of(truth, c.patient_id) for c in truth.cases()}
    assert any(s.startswith("ra-not-active/") for s in strata) and any(s.startswith("ra-active/") for s in strata)
    for stratum in strata:
        pool = [c.patient_id for c in truth.cases() if stratum_of(truth, c.patient_id) == stratum]
        sizes = {p: len(truth.store.chart(p).documents) for p in pool}
        picked = [p for p, s in selected if s == stratum]
        assert min(sizes[p] for p in picked) == min(sizes.values())
        assert max(sizes[p] for p in picked) == max(sizes.values())
    assert select_patients(truth) == selected  # deterministic


@needs_cohort
def test_expected_labels_come_from_ground_truth(truth):
    dataset = build_dataset(truth)
    by_id = {c.inputs.patient_id: c for c in dataset.cases}
    loyd = by_id[resolve_patient(truth, "Loyd638")]
    assert loyd.expected_output.kinds == {
        "ra_diagnosis": "evidence", "dmard_trial": "gap", "active_disease": "evidence",
        "tb_screening": "gap", "hepatitis_b_screening": "gap",
    }
    for case in dataset.cases:
        kinds = case.expected_output.kinds
        assert kinds["tb_screening"] == kinds["hepatitis_b_screening"] == "gap"
        ra, trial, notes = case.metadata["stratum"].split("/")
        assert (kinds["ra_diagnosis"] == "evidence") == (ra == "ra-active")
        assert (kinds["dmard_trial"] == "evidence") == (trial == "trial-shown")
        assert (kinds["active_disease"] == "evidence") == (notes == "active-notes")


@needs_cohort
def test_a_named_patient_is_always_included_even_outside_the_default_selection(truth):
    aaron = resolve_patient(truth, "Aaron697")
    assert aaron not in [p for p, _ in select_patients(truth)]
    [case] = build_dataset(truth, [aaron]).cases
    assert case.inputs.patient_id == aaron and case.metadata["stratum"] == "ra-active/trial-shown/active-notes"


@needs_cohort
def test_case_names_carry_no_patient_names(truth):
    for case in build_dataset(truth).cases:
        assert re.fullmatch(r"[a-z-]+/[a-z-]+/[a-z-]+ · [0-9a-f]{8}", case.name), case.name


def test_model_specs_are_parsed_and_validated():
    _, provider, model = client_for("anthropic:claude-sonnet-5")
    assert (provider, model) == ("anthropic", "claude-sonnet-5")
    for bad in ("claude-sonnet-5", "openai:gpt-x", "anthropic:"):
        with pytest.raises(SystemExit):
            client_for(bad)


# --- evaluating with scripted models ------------------------------------------------------------

async def evaluate(truth, llm, names, repeat=1):
    ids = [resolve_patient(truth, n) for n in names]
    dataset = build_dataset(truth, ids)
    async with connect(None) as session:
        return await dataset.evaluate(make_task(session, llm, truth), name="scripted",
                                      repeat=repeat, max_concurrency=2)


def flat(case):
    return {**{k: v.value for k, v in case.scores.items()}, **{k: v.value for k, v in case.assertions.items()}}


@needs_cohort
@pytest.mark.anyio
async def test_a_faithful_model_passes_every_evaluator_on_every_repeat(truth):
    report = await evaluate(truth, Scripted(faithful_from_prompt), ["Loyd638", "Aaron697"], repeat=2)
    assert len(report.cases) == 4 and not report.failures
    for case in report.cases:
        row = flat(case)
        assert row["completed"] and row["decisions_correct"] and row["packet_fully_correct"]
        assert row["verify_matches_truth"]
        assert (row["citation_resolution_rate"], row["gap_accuracy"]) == (1.0, 1.0)


@needs_cohort
@pytest.mark.anyio
async def test_an_overclaiming_model_is_caught_by_the_evaluators(truth):
    report = await evaluate(truth, Scripted(overclaiming_from_prompt), ["Loyd638"])
    [case] = report.cases
    row = flat(case)
    assert row["completed"] and row["verify_matches_truth"]  # the harness is fine; the model is not
    assert row["decisions_correct"] is False and row["packet_fully_correct"] is False
    assert row["citation_resolution_rate"] == pytest.approx(0.5)


@needs_cohort
@pytest.mark.anyio
async def test_a_failed_run_reports_completed_false_and_no_scores(truth):
    report = await evaluate(truth, Refusing(), ["Loyd638"])
    [case] = report.cases
    assert flat(case) == {"completed": False, "verify_matches_truth": False}
    assert "LLMRefusal" in case.output.error


@needs_cohort
@pytest.mark.anyio
async def test_what_logfire_would_store_holds_nothing_identifying(truth):
    report = await evaluate(truth, Scripted(faithful_from_prompt), ["Loyd638"])
    [case] = report.cases
    patient_id = case.inputs.patient_id  # the input is the case key; the output must be short
    assert len(case.output.patient_id) == 8
    chart = truth.store.chart(patient_id)
    stored = json.dumps({"name": case.name, "output": case.output.__dict__, "metadata": case.metadata},
                        default=str)
    real = {*chart.patient.name.split(), chart.patient.birth_date, patient_id,
            *(c.id for c in chart.conditions), *(m.id for m in chart.medication_requests)}
    assert not [v for v in real if v and re.search(rf"(?<![\w-]){re.escape(v)}(?![\w-])", stored)]
