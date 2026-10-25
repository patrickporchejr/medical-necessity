from collections import Counter
from datetime import date

import pytest
import yaml

from agreement import (
    CLASSES,
    CLASSIFICATIONS_FILE,
    COMPARISONS,
    LABELS_DIR,
    NO_VALID_REPLY,
    PENDING,
    REPORT_FILE,
    RUNS_DISAGREED,
    build,
    build_items,
    check_classifications,
    disagreement_keys,
    flatten,
    run_to_run,
    tally,
    unlabeled_why,
)
from app.config import settings
from dataset import HARD_DIR

CRITERIA = ("ra_diagnosis", "active_disease")


def label(status, runs=None, reason="r"):
    out = {"status": status, "records": [], "reason": reason}
    if runs is not None:
        out["runs"] = [{"status": s, "records": [], "reason": "x"} for s in runs]
    return out


def fixture():
    """Two held-out cases and two hard cases, with every outcome the report distinguishes."""
    held_out = {"cases": {
        "aaaaaaaa": {"ra_diagnosis": label("evidence"), "active_disease": label("gap")},
        "bbbbbbbb": {"ra_diagnosis": label("unlabeled", ["evidence", "gap"]),
                     "active_disease": label("unlabeled", ["unlabeled", "unlabeled"])},
    }}
    hard = {"cases": {
        "cccccccc": {"ra_diagnosis": label("evidence"), "active_disease": label("gap")},
        "dddddddd": {"ra_diagnosis": label("gap"), "active_disease": label("gap")},
    }}
    spec = lambda expected, targeted: {"expected": expected, "records": [], "reason": "r", "targeted": targeted}
    cases = [
        {"name": "neg", "kind": "negation", "key": "cccccccc",
         "criteria": {"ra_diagnosis": spec("evidence", False), "active_disease": spec("gap", True)}},
        {"name": "inj", "kind": "injection", "key": "dddddddd",
         "criteria": {"ra_diagnosis": spec("evidence", False), "active_disease": spec(PENDING, True)}},
    ]
    # The rule: active disease everywhere (it reads negated notes), RA everywhere.
    computed = lambda split, key, cid: "evidence"
    return held_out, hard, cases, computed


def test_unlabeled_is_split_by_why():
    assert unlabeled_why(label("gap")) is None
    assert unlabeled_why(label("unlabeled", ["evidence", "gap"])) == RUNS_DISAGREED
    assert unlabeled_why(label("unlabeled", ["evidence", "unlabeled"])) == NO_VALID_REPLY
    assert unlabeled_why(label("unlabeled")) == NO_VALID_REPLY  # failed the same way in every run


def test_each_comparison_counts_agreement_and_keeps_unlabeled_and_pending_apart():
    items = build_items(*fixture())
    by = {c.name: tally(c.items(items), c.side, c.reference) for c in COMPARISONS}
    held, differ = by["held-out: model vs computed"]
    assert (held.agree, held.compared) == (1, 2)  # aaaaaaaa: RA agrees, active disease differs
    assert held.unlabeled == Counter({RUNS_DISAGREED: 1, NO_VALID_REPLY: 1})
    assert [(i.key, i.criterion) for i in differ] == [("aaaaaaaa", "active_disease")]
    targeted, _ = by["hard, targeted: model vs answer"]
    assert (targeted.agree, targeted.compared, targeted.pending) == (1, 1, 1)
    rule, differ = by["hard, targeted: computed vs answer"]
    assert (rule.agree, rule.compared, rule.pending) == (0, 1, 1)
    assert [(i.key, i.criterion) for i in differ] == [("cccccccc", "active_disease")]
    wiring, differ = by["hard, untargeted (wiring): model vs answer"]
    assert (wiring.agree, wiring.compared) == (1, 2) and differ[0].key == "dddddddd"
    assert str(wiring) == "1 of 2 (50%)"


def test_run_to_run_counts_only_labels_both_runs_gave():
    held_out, hard, _, _ = fixture()
    r = run_to_run(held_out)
    assert r["all"] == Counter({"agreed": 2, "disagreed": 1, "no pair": 1})
    assert r["ra_diagnosis"] == Counter({"agreed": 1, "disagreed": 1})
    assert run_to_run(hard)["all"] == Counter({"agreed": 4})


def test_every_disagreement_needs_a_classification_and_nothing_else_may_have_one():
    keys = disagreement_keys(build_items(*fixture()))
    assert set(keys) == {("held_out", "aaaaaaaa", "active_disease", "model"),
                         ("hard", "cccccccc", "active_disease", "computed"),
                         ("hard", "dddddddd", "ra_diagnosis", "model")}
    full = {"held_out": {"aaaaaaaa": {"active_disease": {"model": {"class": "real ambiguity", "why": "w"}}}},
            "hard": {"cccccccc": {"active_disease": {"computed": {"class": "rule finding", "why": "w"}}},
                     "dddddddd": {"ra_diagnosis": {"model": {"class": "suspected labeler error", "why": "w"}}}}}
    assert len(check_classifications(keys, full)) == 3
    missing = {"hard": full["hard"]}
    with pytest.raises(SystemExit, match="no classification for held_out/aaaaaaaa/active_disease/model"):
        check_classifications(keys, missing)
    stale = {**full, "held_out": {**full["held_out"], "zzzzzzzz": {"ra_diagnosis": {"model": {
        "class": "rule finding", "why": "w"}}}}}
    with pytest.raises(SystemExit, match="classified but not a disagreement: held_out/zzzzzzzz"):
        check_classifications(keys, stale)
    bad = {**full, "hard": {**full["hard"], "cccccccc": {"active_disease": {"computed": {"class": "bug", "why": "w"}}}}}
    with pytest.raises(SystemExit, match="class must be one of"):
        check_classifications(keys, bad)


def test_the_committed_classifications_name_labeled_cases_and_known_classes():
    flat = flatten(yaml.safe_load(CLASSIFICATIONS_FILE.read_text()))
    labels = {s: yaml.safe_load((LABELS_DIR / f"{s}.yaml").read_text())["cases"] for s in ("held_out", "hard")}
    assert flat
    for (split, key, cid, side), entry in flat.items():
        assert cid in labels[split][key], (split, key, cid)
        assert side in ("model", "computed") and (split == "hard" or side == "model")
        assert entry["class"] in CLASSES and entry["why"].strip()


def test_the_report_refuses_an_as_of_date_other_than_the_labels(monkeypatch):
    monkeypatch.setattr(settings, "as_of_date", date(2026, 10, 25))
    with pytest.raises(SystemExit, match="judged as of 2026-09-20"):
        build()


needs_both = pytest.mark.skipif(
    not any(settings.fhir_dir.glob("*.json")) or not any(HARD_DIR.glob("*.json")),
    reason="Synthea cohort or hard split not built")


@needs_both
@pytest.mark.cohort
def test_the_committed_report_is_what_the_command_writes():
    # Reads the whole cohort (the known-patterns counts), so it is opt-in like the other cohort-wide tests.
    assert REPORT_FILE.read_text() == build()
