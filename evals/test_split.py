import re
from datetime import date
from types import SimpleNamespace

import pytest
import yaml

import experiment
from dataset import open_ground_truth
from experiment import (
    CASES_PER_STRATUM,
    HELD_OUT_FILE,
    build_examples,
    load_held_out,
    patient_key,
    run_config,
    select_patients,
    split_of,
    spread,
    stratum_of,
    summarize,
)
from run import resolve_patient
from split import compute_held_out, file_text, held_out_count, pick_held_out, seen_before_split
from app.config import settings

AS_OF = date(2026, 9, 20)
needs_cohort = pytest.mark.skipif(
    not any(settings.fhir_dir.glob("*.json")), reason="Synthea cohort not generated"
)


# --- the committed file -------------------------------------------------------------------------

def test_the_held_out_file_is_sorted_unique_keys_under_the_method_it_was_made_by():
    keys = load_held_out()
    assert all(re.fullmatch(r"[0-9a-f]{8}", k) for k in keys) and keys == sorted(set(keys))
    assert 20 <= len(keys) <= 30  # small enough to hand-label in G1
    # the comment at the top is the method in split.py, word for word
    assert HELD_OUT_FILE.read_text() == file_text(keys)


def test_no_held_out_case_was_run_before_the_split():
    seen = seen_before_split()
    assert "631bf667" in seen  # Loyd638, in every earlier run: the scan finds what it should
    assert not seen & set(load_held_out())


def test_a_malformed_held_out_file_is_an_error(tmp_path):
    for body in ("bbbbbbbb\naaaaaaaa\n", "aaaaaaaa\naaaaaaaa\n", "aaaaaaa\n"):
        (tmp_path / "h.txt").write_text("# method\n" + body)
        with pytest.raises(ValueError, match="sorted, unique 8-character"):
            load_held_out(tmp_path / "h.txt")


# --- the method ---------------------------------------------------------------------------------

def ids(stratum: str, n: int) -> list[tuple[int, str]]:
    """n patients in a stratum, chart sizes 10, 11, ... (so the smallest and largest are known)."""
    return [(10 + i, f"{stratum[:1]}{i:07d}-0000") for i in range(n)]


def test_held_out_sizes_per_situation():
    assert [held_out_count(n) for n in (1, 5, 9, 10, 14, 25, 38, 66, 78)] == [0, 0, 0, 3, 3, 3, 4, 7, 8]


def test_small_situations_stay_whole_and_the_extremes_and_seen_cases_are_never_held_out():
    groups = {"a": ids("a", 78), "b": ids("b", 5), "c": ids("c", 1), "d": ids("d", 14)}
    seen = {patient_key(groups["a"][1][1]), patient_key(groups["a"][2][1])}
    picked = pick_held_out(groups, seen)
    assert {s: len(p) for s, p in picked.items()} == {"a": 8, "b": 0, "c": 0, "d": 3}
    for stratum, members in groups.items():
        ordered = sorted(members)
        assert ordered[0][1] not in picked[stratum] and ordered[-1][1] not in picked[stratum]
    assert not {patient_key(p) for p in picked["a"]} & seen
    assert pick_held_out(groups, seen) == picked  # fixed seed: the same draw every time
    # the draw does not depend on the order the cohort lists its patients in
    assert pick_held_out({s: m[::-1] for s, m in groups.items()}, seen) == picked


def test_spread_takes_both_ends_and_evenly_between():
    assert spread(list(range(10)), 5) == [0, 2, 4, 7, 9]
    assert spread(list(range(3)), 5) == [0, 1, 2]
    assert spread(list(range(5)), 5) == [0, 1, 2, 3, 4]


# --- selection by split, on a fake cohort -------------------------------------------------------

@pytest.fixture
def fake(monkeypatch, tmp_path):
    """12 cases in stratum s (notes 10..21) and 2 in t; s's 0000000b and 00000003 held out."""
    pids = {f"{i:08x}-rest": ("s" if i < 12 else "t") for i in range(14)}
    notes = {p: 10 + i for i, p in enumerate(pids)}
    path = tmp_path / "held_out.txt"
    path.write_text("# method\n00000003\n0000000b\n")
    monkeypatch.setattr(experiment, "HELD_OUT_FILE", path)
    labels = tmp_path / "held_out.yaml"  # what the held-out split is scored against (G1.8)
    label = {"ra_diagnosis": {"status": "gap", "records": [], "reason": "r"}}
    labels.write_text(yaml.safe_dump({"cases": {"00000003": label, "0000000b": label}}))
    monkeypatch.setattr(experiment, "LABELS_FILE", labels)
    monkeypatch.setattr(experiment, "stratum_of", lambda truth, pid: pids[pid])
    truth = SimpleNamespace(
        cases=lambda: [SimpleNamespace(patient_id=p) for p in pids],
        store=SimpleNamespace(chart=lambda p: SimpleNamespace(documents=[None] * notes[p])),
        established=lambda p, cid: False, criterion_ids=["ra_diagnosis"],
    )
    return truth, path


def test_the_working_split_never_holds_a_held_out_case_and_keeps_both_ends(fake):
    truth, _ = fake
    work = select_patients(truth)
    assert work == select_patients(truth, "work")
    keys = [patient_key(p) for p, _ in work]
    assert not {"00000003", "0000000b"} & set(keys)
    s = [patient_key(p) for p, st in work if st == "s"]
    assert len(s) == CASES_PER_STRATUM == 5 and s[0] == "00000000" and s[-1] == "0000000a"
    assert [patient_key(p) for p, st in work if st == "t"] == ["0000000c", "0000000d"]  # fewer than 5: all


def test_the_held_out_split_is_every_held_out_case_and_nothing_else(fake):
    truth, _ = fake
    assert [(patient_key(p), s) for p, s in select_patients(truth, "held_out")] == [
        ("00000003", "s"), ("0000000b", "s")]
    with pytest.raises(ValueError, match="split must be"):
        select_patients(truth, "all")


def test_a_held_out_file_for_another_cohort_is_an_error_not_a_smaller_split(fake):
    truth, path = fake
    path.write_text("00000003\nffffffff\n")
    with pytest.raises(ValueError, match="not in this cohort"):
        select_patients(truth)


def test_a_held_out_case_cannot_be_run_by_name_from_the_working_split(fake):
    truth, _ = fake
    held = "00000003-rest"
    with pytest.raises(SystemExit, match="not in the work split.*--split held_out"):
        build_examples(truth, [held])
    with pytest.raises(SystemExit, match="not in the held_out split"):
        build_examples(truth, ["00000000-rest"], split="held_out")
    [example] = build_examples(truth, [held], split="held_out")
    assert example.metadata["split"] == "held_out"
    # its LangSmith example id is not the one the working dataset would give the same key
    [same_key_as_work] = build_examples(truth, ["00000000-rest"])
    assert same_key_as_work.metadata["split"] == "work" and example.id != same_key_as_work.id


def test_the_config_and_the_summary_name_the_split(fake):
    truth, _ = fake
    truth.store.patient_ids = lambda: [c.patient_id for c in truth.cases()]
    for which, dataset in (("work", "prior-auth-adalimumab-ra"), ("held_out", "prior-auth-adalimumab-ra-held-out")):
        examples = build_examples(truth, split=which)
        config = run_config("anthropic", "m", as_of=AS_OF, repeat=1, examples=examples, truth=truth, split=which)
        assert (config["split"], config["dataset"]) == (which, dataset)
        assert summarize("anthropic:m", "anthropic", "m", [], config)["split"] == which


# --- the real cohort ----------------------------------------------------------------------------

@needs_cohort
@pytest.mark.cohort
def test_the_committed_split_is_what_the_method_gives_for_this_cohort():
    truth = open_ground_truth(AS_OF)
    assert compute_held_out(truth) == load_held_out()
    held = set(load_held_out())
    assert {patient_key(p) for p, _ in select_patients(truth, "held_out")} == held
    counts = {}
    for p, which in split_of(truth).items():
        counts.setdefault(stratum_of(truth, p), {"work": 0, "held_out": 0})[which] += 1
    for stratum, c in counts.items():
        total = c["work"] + c["held_out"]
        assert c["held_out"] == held_out_count(total), stratum
    assert sum(c["held_out"] for c in counts.values()) == len(held)


@needs_cohort
def test_the_patients_the_tests_name_are_in_the_working_split():
    truth = open_ground_truth(AS_OF)
    in_split = split_of(truth)
    for name in ("Loyd638", "Aaron697", "Denis399"):
        assert in_split[resolve_patient(truth, name)] == "work", name

