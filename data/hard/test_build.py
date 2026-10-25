"""The hard split's spec and build.

    cd api && uv run pytest -q ../data/hard

The spec tests need nothing generated. The build test needs the cohort: it reads HARD_BASE_FHIR,
else data/synthea/fhir/, and is skipped when neither has bundles.
"""

import copy
import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build  # noqa: E402

FHIR_DIR = Path(os.environ.get("HARD_BASE_FHIR") or build.DEFAULT_FHIR)


@pytest.fixture(scope="module")
def cases():
    return build.load_cases()


def test_spec_passes(cases):
    assert build.check_spec(cases) == []


def test_every_kind_and_both_sparse_situations(cases):
    kinds = {c["kind"] for c in cases}
    assert kinds == set(build.KINDS)
    situations = [c["situation"] for c in cases if c["kind"] == "sparse-situation"]
    assert situations.count("ra-active/trial-not-shown/no-active-notes") >= build.SPARSE_MIN
    assert situations.count("ra-not-active/trial-not-shown/active-notes") >= build.SPARSE_MIN


def test_every_answer_is_evidence_or_gap(cases):
    # D21 settled the one call left for CPG1; no pending answers remain.
    assert {a["expected"] for c in cases for a in c["criteria"].values()} == {"evidence", "gap"}


def test_targeted_marks_what_each_case_is_scored_on(cases):
    for case in cases:
        answers = case["criteria"].values()
        assert all(isinstance(a["targeted"], bool) for a in answers), case["name"]
        assert any(a["targeted"] for a in answers), case["name"]
        for a in answers:
            assert a["targeted"] != a["reason"].startswith(build.UNCHANGED), (case["name"], a["reason"])


def test_ids_follow_the_contract():
    assert build.patient_id("negation-denies") == str(uuid.uuid5(uuid.NAMESPACE_URL, "hard/negation-denies"))
    assert build.record_id("negation-denies", 0) == str(uuid.uuid5(uuid.NAMESPACE_URL, "hard/negation-denies/0"))


@pytest.mark.parametrize("breakage, message", [
    (lambda c: c.__setitem__("base", sorted(build.held_out_keys())[0]), "held_out"),
    (lambda c: c.__setitem__("key", "00000000"), "is not uuid5"),
    (lambda c: c["criteria"].pop("tb_screening"), "criteria"),
    (lambda c: c["criteria"]["ra_diagnosis"].__setitem__("expected", "maybe"), "not in"),
    (lambda c: c["edits"][0].__setitem__("date", "2026-12-01"), "on or before"),
    (lambda c: c["criteria"]["ra_diagnosis"].pop("targeted"), "true or false"),
    (lambda c: c["criteria"]["ra_diagnosis"].__setitem__("targeted", "yes"), "true or false"),
    (lambda c: [a.__setitem__("targeted", False) for a in c["criteria"].values()], "no targeted criterion"),
    (lambda c: c["criteria"]["ra_diagnosis"].__setitem__("targeted", True), "must not start"),
    (lambda c: c["criteria"]["active_disease"].__setitem__("targeted", False), "must start"),
])
def test_spec_catches(cases, breakage, message):
    broken = copy.deepcopy(cases)
    breakage(broken[0])
    assert any(message in f for f in build.check_spec(broken))


@pytest.mark.skipif(not any(FHIR_DIR.glob("*.json")), reason=f"no cohort bundles in {FHIR_DIR}")
def test_build_check_passes():
    assert build.check(FHIR_DIR) == []
