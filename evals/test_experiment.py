import json
import re
from datetime import date

import pytest

from dataset import open_ground_truth
from experiment import (
    build_examples,
    client_for,
    patient_key,
    run_experiment,
    score_case,
    select_patients,
    stratum_of,
)
from run import connect, resolve_patient
from test_run import Refusing, Scripted, faithful_from_prompt, overclaiming_from_prompt
from app.config import settings
from app.observability import get_client

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
    examples = build_examples(truth)
    by_key = {e.inputs["patient_key"]: e for e in examples}
    loyd = by_key[patient_key(resolve_patient(truth, "Loyd638"))]
    assert loyd.outputs["kinds"] == {
        "ra_diagnosis": "evidence", "dmard_trial": "gap", "active_disease": "evidence",
        "tb_screening": "gap", "hepatitis_b_screening": "gap",
    }
    for example in examples:
        kinds = example.outputs["kinds"]
        assert kinds["tb_screening"] == kinds["hepatitis_b_screening"] == "gap"
        ra, trial, notes = example.metadata["stratum"].split("/")
        assert (kinds["ra_diagnosis"] == "evidence") == (ra == "ra-active")
        assert (kinds["dmard_trial"] == "evidence") == (trial == "trial-shown")
        assert (kinds["active_disease"] == "evidence") == (notes == "active-notes")


@needs_cohort
def test_a_named_patient_is_always_included_even_outside_the_default_selection(truth):
    aaron = resolve_patient(truth, "Aaron697")
    assert aaron not in [p for p, _ in select_patients(truth)]
    [example] = build_examples(truth, [aaron])
    assert example.inputs == {"patient_key": patient_key(aaron)}
    assert example.metadata["stratum"] == "ra-active/trial-shown/active-notes"


@needs_cohort
def test_examples_carry_a_short_key_and_no_patient_names(truth):
    ids = [e.id for e in build_examples(truth)]
    assert len(set(ids)) == len(ids) and ids == [e.id for e in build_examples(truth)]  # stable ids
    for example in build_examples(truth):
        assert re.fullmatch(r"[a-z-]+/[a-z-]+/[a-z-]+ · [0-9a-f]{8}", example.metadata["name"]), example.metadata["name"]
        assert re.fullmatch(r"[0-9a-f]{8}", example.inputs["patient_key"])


def test_model_specs_are_parsed_and_validated():
    _, provider, model = client_for("anthropic:claude-sonnet-5")
    assert (provider, model) == ("anthropic", "claude-sonnet-5")
    for bad in ("claude-sonnet-5", "openai:gpt-x", "anthropic:"):
        with pytest.raises(SystemExit):
            client_for(bad)


# --- scoring ------------------------------------------------------------------------------------

def result(**over):
    base = dict(ok=True, citation_resolution_rate=1.0, citation_level_rate=1.0, gap_accuracy=1.0,
                assertions=[{"criterion_id": "a", "kind": "gap", "truth_resolved": True}],
                unaddressed=[], citations_repaired=0, verify_agrees_with_truth=True)
    return base | over


def test_a_correct_packet_scores_true_on_everything():
    scores = score_case(result(), {"a": "gap"})
    assert scores == {"completed": True, "citation_resolution_rate": 1.0, "citation_level_rate": 1.0,
                      "gap_accuracy": 1.0, "decisions_correct": True, "packet_fully_correct": True,
                      "ids_well_formed": True, "verify_matches_truth": True}


def test_a_wrong_call_a_repaired_id_and_an_unaddressed_criterion_are_each_visible():
    assert score_case(result(), {"a": "evidence"})["decisions_correct"] is False
    assert score_case(result(citations_repaired=2), {"a": "gap"})["ids_well_formed"] is False
    assert score_case(result(unaddressed=["b"]), {"a": "gap"})["packet_fully_correct"] is False
    assert score_case(result(assertions=[{"criterion_id": "a", "kind": "gap", "truth_resolved": False}]),
                      {"a": "gap"})["packet_fully_correct"] is False
    assert "gap_accuracy" not in score_case(result(gap_accuracy=None), {"a": "gap"})


def test_a_failed_run_gets_completed_false_and_no_other_scores():
    assert score_case(result(ok=False), {"a": "gap"}) == {"completed": False, "verify_matches_truth": False}


# --- evaluating with scripted models ------------------------------------------------------------

async def evaluate(truth, llm, names, repeat=1):
    ids = [resolve_patient(truth, n) for n in names]
    async with connect(None) as session:
        return await run_experiment(session, llm, truth, build_examples(truth, ids), name="scripted",
                                    repeat=repeat, concurrency=2, client=get_client())


@needs_cohort
@pytest.mark.anyio
async def test_a_faithful_model_passes_every_evaluator_on_every_repeat(truth):
    rows = await evaluate(truth, Scripted(faithful_from_prompt), ["Loyd638", "Aaron697"], repeat=2)
    assert len(rows) == 4 and not [r for r in rows if r.error]
    for row in rows:
        s = row.scores
        assert s["completed"] and s["decisions_correct"] and s["packet_fully_correct"]
        assert s["verify_matches_truth"]
        assert (s["citation_resolution_rate"], s["gap_accuracy"]) == (1.0, 1.0)


@needs_cohort
@pytest.mark.anyio
async def test_an_overclaiming_model_is_caught_by_the_evaluators(truth):
    [row] = await evaluate(truth, Scripted(overclaiming_from_prompt), ["Loyd638"])
    s = row.scores
    assert s["completed"] and s["verify_matches_truth"]  # the harness is fine; the model is not
    assert s["decisions_correct"] is False and s["packet_fully_correct"] is False
    assert s["citation_resolution_rate"] == pytest.approx(0.5)


@needs_cohort
@pytest.mark.anyio
async def test_a_failed_run_reports_completed_false_and_no_scores(truth):
    [row] = await evaluate(truth, Refusing(), ["Loyd638"])
    assert row.scores == {"completed": False, "verify_matches_truth": False}
    assert "LLMRefusal" in row.output["error"]


@needs_cohort
@pytest.mark.anyio
async def test_what_langsmith_would_store_holds_nothing_identifying(truth, ls):
    patient_id = resolve_patient(truth, "Loyd638")
    [row] = await evaluate(truth, Scripted(faithful_from_prompt), ["Loyd638"])
    assert row.key == patient_id[:8] and row.output["patient_id"] == patient_id[:8]
    chart = truth.store.chart(patient_id)
    sent = json.dumps(ls.runs, default=str)
    assert ls.runs, "the experiment recorded nothing, so this check would pass vacuously"
    real = {*chart.patient.name.split(), chart.patient.birth_date, patient_id,
            *(c.id for c in chart.conditions), *(m.id for m in chart.medication_requests)}
    assert not [v for v in real if v and re.search(rf"(?<![\w-]){re.escape(v)}(?![\w-])", sent)]
    # inputs are the short key, and no run carries the packet or the note text
    assert all(r["inputs"] in ({}, {"patient_key": patient_id[:8]}) for r in ls.runs)
    assert '"packet"' not in sent
