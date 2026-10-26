import hashlib
import json
from collections import Counter
import re
import subprocess
import uuid
from datetime import date
from types import SimpleNamespace

import pytest
import yaml
from langsmith import schemas
from langsmith.utils import LangSmithAuthError, LangSmithNotFoundError

from dataset import HARD_DIR, labels_as_of, open_ground_truth, pinned_as_of
from experiment import (
    CASES_PER_STRATUM,
    LABELS_FILE,
    Row,
    build_examples,
    client_for,
    cohort_fingerprint,
    dataset_name,
    example_id,
    git_commit,
    load_hard_cases,
    load_held_out,
    open_split,
    reference,
    truth_for,
    package_version,
    patient_key,
    prompt_files,
    run_config,
    run_experiment,
    sync_dataset,
    score_case,
    select_patients,
    sha256_of,
    stratum_of,
    summarize,
)
from experiment import main as experiment_main
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
@pytest.mark.cohort
def test_the_dataset_covers_every_situation_with_a_small_and_a_large_chart(truth):
    selected = select_patients(truth)
    strata = {s for _, s in selected}
    # every situation that occurs in the cohort is covered, including a resolved diagnosis
    assert strata == {stratum_of(truth, c.patient_id) for c in truth.cases()}
    assert any(s.startswith("ra-not-active/") for s in strata) and any(s.startswith("ra-active/") for s in strata)
    held = set(load_held_out())
    assert not held & {patient_key(p) for p, _ in selected}  # the working split reads no held-out case
    for stratum in strata:
        pool = [c.patient_id for c in truth.cases() if stratum_of(truth, c.patient_id) == stratum]
        sizes = {p: len(truth.store.chart(p).documents) for p in pool}
        picked = [p for p, s in selected if s == stratum]
        assert len(picked) == min(CASES_PER_STRATUM, len([p for p in pool if patient_key(p) not in held]))
        # the cohort's smallest and largest chart, not only the working split's: the split keeps them
        assert min(sizes[p] for p in picked) == min(sizes.values())
        assert max(sizes[p] for p in picked) == max(sizes.values())
    assert select_patients(truth) == selected  # deterministic


@needs_cohort
@pytest.mark.cohort
def test_expected_labels_come_from_ground_truth(truth):
    [loyd] = build_examples(truth, [resolve_patient(truth, "Loyd638")])
    assert loyd.outputs["kinds"] == {
        "ra_diagnosis": "evidence", "dmard_trial": "gap", "active_disease": "evidence",
        "tb_screening": "gap", "hepatitis_b_screening": "gap",
    }
    for example in build_examples(truth):
        kinds = example.outputs["kinds"]
        assert kinds["tb_screening"] == kinds["hepatitis_b_screening"] == "gap"
        ra, trial, notes = example.metadata["stratum"].split("/")
        assert (kinds["ra_diagnosis"] == "evidence") == (ra == "ra-active")
        assert (kinds["dmard_trial"] == "evidence") == (trial == "trial-shown")
        assert (kinds["active_disease"] == "evidence") == (notes == "active-notes")


@needs_cohort
@pytest.mark.cohort
def test_a_named_patient_is_always_included_even_outside_the_default_selection(truth):
    aaron = resolve_patient(truth, "Aaron697")
    assert aaron not in [p for p, _ in select_patients(truth)]
    [example] = build_examples(truth, [aaron])
    assert example.inputs == {"patient_key": patient_key(aaron)}
    assert example.metadata["stratum"] == "ra-active/trial-shown/active-notes"


@needs_cohort
@pytest.mark.cohort
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


# --- the run config -----------------------------------------------------------------------------

def fake_truth(patient_ids):
    return SimpleNamespace(store=SimpleNamespace(patient_ids=lambda: list(patient_ids)))


def fake_examples(keys):
    return [SimpleNamespace(inputs={"patient_key": k}) for k in keys]


def test_the_config_block_depends_only_on_inputs_and_names_everything_a_result_depends_on():
    ids = ["c2b0e5c3-0000-4000-8000-000000000002", "0a1b2c3d-0000-4000-8000-000000000001"]
    make = lambda: run_config("anthropic", "claude-haiku-4-5-20251001", as_of=AS_OF, repeat=3,
                              examples=fake_examples(["c2b0e5c3", "0a1b2c3d"]), truth=fake_truth(ids))
    config = make()
    assert config == make()  # a rerun of the same inputs writes the same block
    assert set(config) == {"provider", "model", "as_of", "repeat", "split", "dataset", "case_keys", "cohort",
                           "prompts_sha256", "criteria_sha256", "reference", "reference_sha256", "git_commit",
                           "genai_prices"}
    assert (config["provider"], config["model"], config["as_of"], config["repeat"]) == (
        "anthropic", "claude-haiku-4-5-20251001", "2026-09-20", 3)
    assert (config["split"], config["dataset"]) == ("work", "prior-auth-adalimumab-ra")  # the default
    assert config["case_keys"] == ["0a1b2c3d", "c2b0e5c3"]
    assert config["cohort"] == {"patients": 2, "sha256": cohort_fingerprint(ids)}
    assert re.fullmatch(r"[0-9a-f]{64}", config["prompts_sha256"])
    assert re.fullmatch(r"[0-9a-f]{64}", config["criteria_sha256"])
    assert config["genai_prices"] == package_version("genai-prices") is not None
    # no full patient id, and nothing that looks like a timestamp
    text = json.dumps(config)
    assert not [i for i in ids if i in text]
    assert not re.search(r"\d{4}-\d{2}-\d{2}T|\d{8}T\d{6}", text)


def test_the_cohort_fingerprint_is_the_sha256_of_the_sorted_newline_joined_ids():
    # the same bytes data/generate.sh hashes when it prints the cohort fingerprint
    assert cohort_fingerprint(["b", "a"]) == hashlib.sha256(b"a\nb\n").hexdigest()
    assert cohort_fingerprint(["a", "b"]) == cohort_fingerprint(["b", "a"])


def test_a_file_hash_changes_with_content_and_name_but_not_listing_order(tmp_path):
    (tmp_path / "a.py").write_text("one")
    (tmp_path / "b.py").write_text("two")
    files = [tmp_path / "a.py", tmp_path / "b.py"]
    before = sha256_of(files, tmp_path)
    assert sha256_of(files[::-1], tmp_path) == before
    (tmp_path / "b.py").write_text("two!")
    assert sha256_of(files, tmp_path) != before
    (tmp_path / "b.py").write_text("two")
    (tmp_path / "b.py").rename(tmp_path / "c.py")
    assert sha256_of([tmp_path / "a.py", tmp_path / "c.py"], tmp_path) != before


def test_the_git_commit_marks_a_dirty_tree_and_is_none_outside_a_checkout(tmp_path):
    assert git_commit(tmp_path) is None
    git = lambda *a: subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t",
                                     *a], check=True, capture_output=True, text=True).stdout.strip()
    git("init", "-q")
    (tmp_path / "f.txt").write_text("x")
    git("add", "f.txt")
    git("commit", "-qm", "init")
    sha = git("rev-parse", "HEAD")
    assert git_commit(tmp_path) == sha
    (tmp_path / "untracked.json").write_text("{}")  # e.g. an eval's own output
    assert git_commit(tmp_path) == sha
    (tmp_path / "f.txt").write_text("y")
    assert git_commit(tmp_path) == f"{sha}-dirty"


def test_the_prompt_hash_covers_the_system_prompt_and_the_rendered_user_message():
    names = {p.relative_to(p.parents[2]).as_posix() for p in prompt_files()}
    assert {"llm/prompts/assemble.py", "graph/nodes/assemble.py"} <= names
    assert all(p.is_file() for p in prompt_files())


def test_a_missing_package_has_no_version():
    assert package_version("no-such-package-for-medical-necessity") is None


def test_the_summary_carries_the_config_block():
    rows = [Row(name="s · 0a1b2c3d", key="0a1b2c3d", scores={"completed": True}, output={}, error=None)]
    config = {"model": "m"}
    assert summarize("anthropic:m", "anthropic", "m", rows, config)["config"] is config


@needs_cohort
@pytest.mark.cohort
def test_the_config_block_for_the_real_dataset_is_stable(truth):
    examples = build_examples(truth)
    config = run_config("gemini", "gemini-3.8-flash", as_of=AS_OF, repeat=3, examples=examples, truth=truth)
    assert config == run_config("gemini", "gemini-3.8-flash", as_of=AS_OF, repeat=3,
                                examples=examples, truth=open_ground_truth(AS_OF))
    assert config["cohort"]["patients"] == len(truth.cases())
    assert config["case_keys"] == sorted(e.inputs["patient_key"] for e in examples)


# --- syncing the dataset ------------------------------------------------------------------------

class FakeLangSmith:
    """Enough of the client to check which examples are created and which are updated."""

    def __init__(self, existing_ids):
        self.stored = {i: None for i in existing_ids}
        self.calls = []
        self.dataset = SimpleNamespace(id=uuid.uuid4())

    def has_dataset(self, **kw):
        return True

    def read_dataset(self, **kw):
        return self.dataset

    def list_examples(self, dataset_id, example_ids):
        return [SimpleNamespace(id=i) for i in example_ids if i in self.stored]

    def update_examples_multipart(self, dataset_id, updates):
        assert all(u.id in self.stored for u in updates), "updated an example that does not exist"
        self.calls.append(("update", [u.id for u in updates]))

    def upsert_examples_multipart(self, upserts):
        assert not [u for u in upserts if u.id in self.stored], "409: created an example that exists"
        self.calls.append(("create", [u.id for u in upserts]))
        self.stored.update({u.id: None for u in upserts})


@needs_cohort
@pytest.mark.cohort
def test_syncing_updates_existing_examples_creates_new_ones_and_survives_a_rerun(truth):
    examples = build_examples(truth)
    first, rest = examples[:1], examples
    client = FakeLangSmith(existing_ids=[examples[0].id])  # one already uploaded by an earlier run
    sync_dataset(client, rest)
    assert client.calls == [("update", [examples[0].id]), ("create", [e.id for e in examples[1:]])]
    client.calls.clear()
    sync_dataset(client, rest)  # a rerun with everything present must only update
    assert client.calls == [("update", [e.id for e in examples])]


class FakeLangSmithNotFound(FakeLangSmith):
    """LangSmith's real answer when none of the requested ids exist: a 404, not an empty list."""

    def list_examples(self, dataset_id, example_ids):
        found = [SimpleNamespace(id=i) for i in example_ids if i in self.stored]
        if not found:
            raise LangSmithNotFoundError("Resource not found for /examples. Examples not found")
        yield from found


def split_examples(n, split="hard"):
    return [schemas.Example(id=example_id(f"{i:08d}", split), dataset_id=uuid.uuid4(),
                            inputs={"patient_key": f"{i:08d}"}, outputs={}, metadata={})
            for i in range(n)]


def test_syncing_a_new_dataset_treats_examples_not_found_as_none_existing():
    examples = split_examples(3)
    client = FakeLangSmithNotFound(existing_ids=[])
    synced = sync_dataset(client, examples, name=dataset_name("hard"))
    assert client.calls == [("create", [e.id for e in examples])]
    assert [e.id for e in synced] == [e.id for e in examples]
    client.calls.clear()
    sync_dataset(client, examples, name=dataset_name("hard"))  # once they exist, a rerun only updates
    assert client.calls == [("update", [e.id for e in examples])]


def test_syncing_does_not_swallow_other_langsmith_errors():
    class Broken(FakeLangSmith):
        def list_examples(self, dataset_id, example_ids):
            raise LangSmithAuthError("401")

    with pytest.raises(LangSmithAuthError):
        sync_dataset(Broken(existing_ids=[]), split_examples(1))


# --- scoring ------------------------------------------------------------------------------------

def packet(**over):
    base = dict(citation_resolution_rate=1.0, citation_level_rate=1.0, gap_accuracy=1.0,
                assertions=[{"criterion_id": "a", "kind": "gap", "truth_resolved": True}], unaddressed=[])
    return base | over


def result(**over):
    base = packet() | dict(ok=True, citations_repaired=0, schema_retries=0, repairs=0, first_pass=packet(),
                           verify_agrees_with_truth=True)
    return base | over


def test_a_correct_packet_scores_true_on_everything():
    scores = score_case(result(), {"a": "gap"})
    assert scores == {"completed": True, "citation_resolution_rate": 1.0, "citation_level_rate": 1.0,
                      "gap_accuracy": 1.0, "decisions_correct": True, "packet_fully_correct": True,
                      "first_pass_citation_resolution_rate": 1.0, "first_pass_citation_level_rate": 1.0,
                      "first_pass_gap_accuracy": 1.0, "first_pass_decisions_correct": True,
                      "first_pass_packet_fully_correct": True, "repaired": 0.0, "decision:a": True,
                      "ids_well_formed": True, "schema_valid_first_try": True, "verify_matches_truth": True}


def test_a_repaired_case_scores_its_first_pass_and_its_final_packet_separately():
    wrong = [{"criterion_id": "a", "kind": "evidence", "truth_resolved": False}]
    first = packet(assertions=wrong, citation_resolution_rate=0.0, gap_accuracy=None)
    scores = score_case(result(repairs=1, first_pass=first), {"a": "gap"})
    assert scores["repaired"] == 1.0
    assert scores["packet_fully_correct"] is True and scores["first_pass_packet_fully_correct"] is False
    assert scores["first_pass_decisions_correct"] is False and scores["first_pass_citation_resolution_rate"] == 0.0
    assert "first_pass_gap_accuracy" not in scores
    # a repair call that failed left the first packet in place: not a repair
    assert score_case(result(repairs=1, repair_failed="LLMRefusal"), {"a": "gap"})["repaired"] == 0.0


def test_a_wrong_call_a_repaired_id_and_an_unaddressed_criterion_are_each_visible():
    assert score_case(result(), {"a": "evidence"})["decisions_correct"] is False
    assert score_case(result(citations_repaired=2), {"a": "gap"})["ids_well_formed"] is False
    assert score_case(result(schema_retries=1), {"a": "gap"})["schema_valid_first_try"] is False
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


def overclaims_until_repaired(prompt: str):
    """Overclaims on the first draft, then answers the repair prompt faithfully."""
    repairing = "An automated check of your previous assertions" in prompt
    return (faithful_from_prompt if repairing else overclaiming_from_prompt)(prompt)


@needs_cohort
@pytest.mark.anyio
async def test_the_repair_loop_is_scored_on_both_passes_so_it_cannot_hide_the_model(truth):
    [row] = await evaluate(truth, Scripted(overclaims_until_repaired), ["Loyd638"])
    s = row.scores
    assert s["repaired"] == 1.0 and s["verify_matches_truth"]
    assert s["packet_fully_correct"] is True and s["first_pass_packet_fully_correct"] is False
    assert s["first_pass_citation_resolution_rate"] == pytest.approx(0.5)
    summary = summarize("anthropic:m", "anthropic", "m", [row])
    assert summary["repaired_runs"] == 1
    assert summary["averages"]["first_pass_packet_fully_correct"] == 0.0


@needs_cohort
@pytest.mark.anyio
async def test_a_failed_run_reports_completed_false_and_no_scores(truth):
    [row] = await evaluate(truth, Refusing(), ["Loyd638"])
    assert row.scores == {"completed": False, "verify_matches_truth": False}
    assert "LLMRefusal" in row.output["error"]


@needs_cohort
@pytest.mark.anyio
async def test_ra_not_active_cases_cost_nothing_and_the_summary_shows_it(truth):
    rows = await evaluate(truth, Scripted(faithful_from_prompt), ["Loyd638", "Denis399"])
    summary = summarize("anthropic:claude-haiku-4-5-20251001", "anthropic", "claude-haiku-4-5-20251001", rows)
    by_route = {c["route"]: c for c in summary["cases"]}
    assert by_route["gap_packet"]["name"].startswith("ra-not-active/")
    assert by_route["gap_packet"]["estimated_cost_usd"] == 0.0 and by_route["assemble"]["estimated_cost_usd"] > 0
    assert summary["routes"]["gap_packet"] == {"runs": 1, "estimated_cost_usd": 0.0}
    assert summary["routes"]["assemble"]["runs"] == 1
    assert all(v for k, v in by_route["gap_packet"]["scores"].items() if k != "repaired")
    assert by_route["gap_packet"]["scores"]["repaired"] == 0.0  # code wrote it: never sent back to a model


def test_a_gap_packet_run_costs_nothing_even_for_a_model_with_no_known_price():
    rows = [Row(name="ra-not-active/x · 0a1b2c3d", key="0a1b2c3d", scores={"completed": True},
                output={"route": "gap_packet", "input_tokens": 0, "output_tokens": 0}, error=None)]
    summary = summarize("anthropic:no-such-model", "anthropic", "no-such-model", rows)
    assert summary["cases"][0]["estimated_cost_usd"] == 0.0
    assert summary["routes"] == {"gap_packet": {"runs": 1, "estimated_cost_usd": 0.0}}


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


# --- latency ------------------------------------------------------------------------------------

def timed_row(route, seconds, error=None):
    return Row(name=f"s · {len(seconds):08d}", key="0a1b2c3d", scores={"completed": error is None},
               output={"route": route, "seconds": seconds}, error=error)


def test_the_summary_carries_p50_and_max_seconds_per_node_over_the_runs_that_reached_it():
    rows = [timed_row("assemble", {"extract": 1.0, "assemble": 4.0, "verify": 0.1}),
            timed_row("assemble", {"extract": 3.0, "assemble": 10.0, "verify": 0.3}),
            timed_row("assemble", {"extract": 2.0, "assemble": 6.0, "verify": 0.2}),
            timed_row("gap_packet", {"extract": 5.0, "gap_packet": 0.01, "verify": 0.4})]
    seconds = summarize("anthropic:m", "anthropic", "m", rows)["seconds"]
    assert seconds == {
        "assemble": {"runs": 3, "p50": 6.0, "max": 10.0},  # the gap_packet run is not a 0 here
        "extract": {"runs": 4, "p50": 2.5, "max": 5.0},
        "gap_packet": {"runs": 1, "p50": 0.01, "max": 0.01},
        "verify": {"runs": 4, "p50": 0.25, "max": 0.4},
    }


def test_a_run_that_failed_or_raised_counts_only_the_nodes_it_finished():
    rows = [timed_row("assemble", {"extract": 1.0, "assemble": 2.0, "verify": 0.1}),
            timed_row(None, {"extract": 3.0}, error="LLMRefusal: declined"),  # failed in assemble
            Row(name="s · x", key="x", scores={}, output={}, error="boom")]  # the target raised
    summary = summarize("anthropic:m", "anthropic", "m", rows)
    assert summary["seconds"]["extract"] == {"runs": 2, "p50": 2.0, "max": 3.0}
    assert summary["seconds"]["assemble"]["runs"] == 1
    assert [c["seconds"] for c in summary["cases"]] == [r.output.get("seconds", {}) for r in rows]
    assert summarize("anthropic:m", "anthropic", "m", [])["seconds"] == {}


@needs_cohort
@pytest.mark.anyio
async def test_scripted_runs_put_per_node_seconds_in_the_summary(truth):
    rows = await evaluate(truth, Scripted(faithful_from_prompt), ["Loyd638", "Denis399"], repeat=2)
    summary = summarize("anthropic:m", "anthropic", "m", rows)
    seconds = summary["seconds"]
    assert set(seconds) == {"extract", "assemble", "gap_packet", "verify"}
    assert {n: s["runs"] for n, s in seconds.items()} == {"extract": 4, "verify": 4, "assemble": 2, "gap_packet": 2}
    for node, stats in seconds.items():
        laps = [c["seconds"][node] for c in summary["cases"] if node in c["seconds"]]
        assert stats["max"] == max(laps) and min(laps) <= stats["p50"] <= stats["max"]
    json.dumps(summary)  # it goes into the summary file as is


# --- scoring per split (G1.8) -------------------------------------------------------------------

def unlabeled(*runs):
    return {"status": "unlabeled", "records": [], "reason": "r",
            "runs": [{"status": s, "records": [], "reason": "x"} for s in runs]}


def test_held_out_is_scored_against_the_labels_and_unlabeled_ones_are_left_out_with_why():
    labels = {"0a1b2c3d": {"a": {"status": "evidence", "records": [], "reason": "r"},
                           "b": unlabeled("evidence", "gap"), "c": unlabeled("unlabeled", "unlabeled")}}
    ref = reference("held_out", "0a1b2c3d", ["a", "b", "c"], labels=labels)
    assert ref["kinds"] == {"a": "evidence"} and ref["wiring"] == {}
    assert ref["excluded"] == {"b": "unlabeled: runs disagreed", "c": "unlabeled: no valid reply"}
    assert ref["reference"].startswith("reference labels")
    with pytest.raises(SystemExit):
        reference("held_out", "ffffffff", ["a"], labels=labels)


def test_hard_is_scored_on_targeted_answers_with_pending_left_out_and_the_rest_a_wiring_check():
    answer = lambda expected, targeted: {"expected": expected, "records": [], "reason": "r", "targeted": targeted}
    cases = [{"key": "0a1b2c3d", "criteria": {"a": answer("gap", True), "b": answer("pending-CPG1", True),
                                              "c": answer("evidence", False)}}]
    ref = reference("hard", "0a1b2c3d", ["a", "b", "c"], cases=cases)
    assert (ref["kinds"], ref["excluded"], ref["wiring"]) == ({"a": "gap"}, {"b": "pending-CPG1"}, {"c": "evidence"})


def test_the_committed_references_count_as_the_label_run_and_cases_say():
    labels = yaml.safe_load(LABELS_FILE.read_text())["cases"]
    ids = ["ra_diagnosis", "dmard_trial", "active_disease", "tb_screening", "hepatitis_b_screening"]
    held = [reference("held_out", k, ids, labels=labels) for k in load_held_out()]
    assert sum(len(r["kinds"]) for r in held) == 115
    assert Counter(w for r in held for w in r["excluded"].values()) == {
        "unlabeled: runs disagreed": 5, "unlabeled: no valid reply": 5}
    cases = load_hard_cases()
    hard = [reference("hard", c["key"], ids, cases=cases) for c in cases]
    assert (sum(len(r["kinds"]) for r in hard), sum(len(r["excluded"]) for r in hard),
            sum(len(r["wiring"]) for r in hard)) == (
        sum(a["targeted"] for c in cases for a in c["criteria"].values()), 0,  # D20, and D21 left none pending
        sum(not a["targeted"] for c in cases for a in c["criteria"].values()))


def calls(**kinds):
    return [{"criterion_id": c, "kind": k, "truth_resolved": True} for c, k in kinds.items()]


def test_a_reference_split_scores_each_criterion_and_keeps_excluded_and_wiring_out_of_the_headline():
    out = result(assertions=calls(a="gap", b="evidence", c="gap"),
                 first_pass=packet(assertions=calls(a="evidence", b="evidence", c="gap")))
    scores = score_case(out, {"a": "gap"}, excluded={"b": "pending-CPG1"}, wiring={"c": "evidence"},
                        computed=False)
    assert scores["decisions_correct"] is True and scores["decision:a"] is True
    assert scores["first_pass_decisions_correct"] is False
    assert scores["wiring:c"] is False and "decision:c" not in scores and "decision:b" not in scores
    # its citation check is against computed ground truth, so it is not scored against a reference
    assert "packet_fully_correct" not in scores and "first_pass_packet_fully_correct" not in scores
    # a call on a criterion the case does not have is still wrong
    assert score_case(result(assertions=calls(a="gap", z="gap")), {"a": "gap"}, computed=False)[
        "decisions_correct"] is False


def test_the_summary_gives_counts_beside_rates_and_what_was_left_out():
    ref = {"reference": "reference labels (evals/labels/held_out.yaml)", "kinds": {"a": "gap", "b": "gap"},
           "excluded": {"c": "unlabeled: runs disagreed"}, "wiring": {}}
    rows = [Row(name=f"s · {k}", key=k, scores={"completed": True, "decisions_correct": ok, "decision:a": True,
                                               "decision:b": ok}, output={}, error=None, expected=ref)
            for k, ok in (("0a1b2c3d", True), ("1b2c3d4e", False), ("1b2c3d4e", True))]
    summary = summarize("scripted:faithful", "scripted", "faithful", rows, {"split": "held_out"})
    assert summary["tallies"]["decisions_correct"] == "2 of 3"
    s = summary["scoring"]
    assert s["against"].startswith("reference labels") and s["cases"] == 2
    assert s["decisions"]["all"] == "5 of 6" and s["decisions"]["per_criterion"] == {"a": "3 of 3", "b": "2 of 3"}
    assert s["criteria_scored"] == 4 and s["criteria_excluded"] == {"unlabeled: runs disagreed": 2}
    assert "wiring" not in s and s["citations_scored_against"] == "computed ground truth"
    json.dumps(summary)


def test_the_hard_split_has_its_own_dataset_and_example_ids():
    assert dataset_name("hard") == "prior-auth-adalimumab-ra-hard"
    assert dataset_name("held_out") == "prior-auth-adalimumab-ra-held-out"
    assert len({example_id("0a1b2c3d", s) for s in ("work", "held_out", "hard")}) == 3


def test_the_scripted_spec_needs_no_model():
    llm, provider, model = client_for("scripted:faithful")
    assert (provider, model, llm.provider) == ("scripted", "faithful", "fake")


needs_hard = pytest.mark.skipif(not any(HARD_DIR.glob("*.json")), reason="hard split not built")


@needs_hard
def test_the_hard_split_reads_its_own_bundles_and_every_case():
    hard = truth_for("hard", AS_OF)
    chosen = select_patients(hard, "hard")
    cases = load_hard_cases()
    assert [patient_key(p) for p, _ in chosen] == [c["key"] for c in cases]
    assert {s for _, s in chosen} == {f"hard/{c['kind']}" for c in cases}
    examples = {e.inputs["patient_key"]: e for e in build_examples(hard, split="hard")}
    injected = examples["977def3e"].outputs  # injection-keyword-line: the injected keyword is not evidence (D21)
    assert injected["kinds"]["active_disease"] == "gap" and injected["excluded"] == {}
    assert injected["wiring"] == {"ra_diagnosis": "evidence"}
    assert examples["e4b60980"].outputs["kinds"] == {"active_disease": "gap"}


@needs_cohort
@needs_hard
@pytest.mark.anyio
async def test_a_scripted_model_runs_on_each_split_and_is_scored_against_that_splits_reference(truth):
    held = next(p for p in truth.store.patient_ids() if patient_key(p) == load_held_out()[0])
    hard = truth_for("hard", AS_OF)
    neg = next(p for p in hard.store.patient_ids() if p.startswith("e4b60980"))  # negation-denies
    for split, t, ids in (("held_out", truth, [held]), ("hard", hard, [neg])):
        async with open_split(split) as session:
            [row] = await run_experiment(session, Scripted(faithful_from_prompt), t,
                                         build_examples(t, ids, split=split), name="scripted", client=get_client())
        assert row.scores["completed"] and row.scores["verify_matches_truth"], row.error
        assert "packet_fully_correct" not in row.scores
        if split == "hard":
            # the rule reads "Denies joint pain" as evidence and the faithful model follows the rule, so the
            # targeted answer (gap) catches it; the untargeted criteria are only the wiring check
            assert row.scores["decision:active_disease"] is False and row.scores["decisions_correct"] is False
            assert all(row.scores[f"wiring:{c}"] for c in ("ra_diagnosis", "dmard_trial", "tb_screening",
                                                          "hepatitis_b_screening"))


@needs_cohort
@needs_hard
@pytest.mark.cohort
def test_a_scripted_experiment_over_all_three_splits_runs_end_to_end(tmp_path):
    assert get_client() is None  # no LangSmith key: nothing is uploaded
    assert experiment_main(["--models", "scripted:faithful", "--split", "all", "--repeat", "1",
                            "--out-dir", str(tmp_path)]) == 0
    files = sorted(tmp_path.glob("experiment_*.json"))
    assert {json.loads(f.read_text())["split"] for f in files} == {"work", "held_out", "hard"}
    for f in files:
        [s] = json.loads(f.read_text())["experiments"]
        assert s["task_failures"] == 0 and " of " in s["scoring"]["decisions"]["all"]


# --- the as-of date the labels were judged at -----------------------------------------------------

def label_headers(tmp_path, held="2026-09-20", hard="2026-09-20"):
    for name, as_of in (("held_out.yaml", held), ("hard.yaml", hard)):
        (tmp_path / name).write_text(f"labeler: m\nas_of: '{as_of}'\ncases: {{}}\n")
    return tmp_path


def test_scoring_against_the_labels_uses_their_as_of_date_and_refuses_another(tmp_path):
    d = label_headers(tmp_path)
    assert labels_as_of(d) == date(2026, 9, 20)
    assert pinned_as_of(None, d) == date(2026, 9, 20)  # AS_OF_DATE unset: the labels' date
    assert pinned_as_of(date(2026, 9, 20), d) == date(2026, 9, 20)
    with pytest.raises(SystemExit, match="AS_OF_DATE is 2026-10-25, but the reference labels were judged as of "
                                         "2026-09-20"):
        pinned_as_of(date(2026, 10, 25), d)
    with pytest.raises(SystemExit, match="disagree"):
        labels_as_of(label_headers(tmp_path, hard="2026-09-21"))


def test_the_committed_labels_are_judged_at_the_cohort_as_of_date():
    assert labels_as_of() == AS_OF


def test_an_experiment_on_a_labeled_split_refuses_a_different_as_of_date(monkeypatch):
    monkeypatch.setattr(settings, "as_of_date", date(2026, 10, 25))
    for split in ("held_out", "hard", "all"):
        with pytest.raises(SystemExit, match="unset AS_OF_DATE or set it to 2026-09-20"):
            experiment_main(["--models", "scripted:faithful", "--split", split])
