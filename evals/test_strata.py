from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

import strata
from dataset import open_ground_truth
from experiment import stratum_of
from strata import ALL_STRATA, absent, format_report, main, strata_counts, under
from app.config import settings

AS_OF = date(2026, 9, 20)
needs_cohort = pytest.mark.skipif(
    not any(settings.fhir_dir.glob("*.json")), reason="Synthea cohort not generated"
)


def fake_truth(strata_by_patient: dict[str, str], monkeypatch):
    """A cohort whose situations are given, so counting is checked without bundles."""
    monkeypatch.setattr(strata, "stratum_of", lambda truth, pid: strata_by_patient[pid])
    return SimpleNamespace(cases=lambda: [SimpleNamespace(patient_id=p) for p in strata_by_patient])


def test_every_possible_situation_is_counted_and_an_empty_one_is_zero(monkeypatch):
    a, b = "ra-active/no-order/active-notes", "ra-not-active/trial-not-shown/no-active-notes"
    counts = strata_counts(fake_truth({"p1": a, "p2": a, "p3": b}, monkeypatch))
    assert list(counts) == ALL_STRATA and len(ALL_STRATA) == 12
    assert counts[a] == 2 and counts[b] == 1 and sum(counts.values()) == 3


def test_a_situation_the_list_does_not_know_is_an_error_not_a_silent_drop(monkeypatch):
    with pytest.raises(ValueError, match="ALL_STRATA"):
        strata_counts(fake_truth({"p1": "ra-active/new-axis/active-notes"}, monkeypatch))


def test_under_names_present_situations_below_the_minimum_and_absent_ones_are_apart():
    counts = dict.fromkeys(ALL_STRATA, 0) | {ALL_STRATA[0]: 5, ALL_STRATA[1]: 4, ALL_STRATA[2]: 1}
    assert under(counts, 5) == [ALL_STRATA[1], ALL_STRATA[2]]
    assert absent(counts) == ALL_STRATA[3:]
    report = format_report(counts, fhir_dir=Path("cohort"), as_of=AS_OF, minimum=5)
    assert "cohort cohort, as of 2026-09-20" in report
    assert "under 5: 2 of the 3 situations in the cohort" in report
    assert f"  {ALL_STRATA[1]} (4)" in report and f"  {ALL_STRATA[2]} (1)" in report
    assert "not in the cohort: 9 of 12 possible" in report
    total = next(line for line in report.splitlines() if line.strip().startswith("total"))
    assert total.split()[-1] == "10"


@needs_cohort
def test_the_report_on_the_current_cohort_counts_every_patient_once(capsys):
    assert main(["--as-of", AS_OF.isoformat()]) == 0
    out = capsys.readouterr().out
    truth = open_ground_truth(AS_OF)
    assert out.startswith(f"cohort {settings.fhir_dir}, as of 2026-09-20")
    total = next(line for line in out.splitlines() if line.strip().startswith("total"))
    assert int(total.split()[-1]) == len(truth.cases())
    for stratum in {stratum_of(truth, c.patient_id) for c in truth.cases()}:
        assert f"  {stratum} " in out
    # counts only: no patient name or id is printed
    assert not [c for c in truth.cases() if c.patient_id[:8] in out or c.name.split()[0] in out]


def test_a_directory_with_no_bundles_is_refused(tmp_path):
    with pytest.raises(SystemExit, match="no patient bundles"):
        main(["--fhir-dir", str(tmp_path)])


def test_the_report_reads_the_cohort_directory_it_is_given(tmp_path, monkeypatch, capsys):
    seen = {}

    class Store:
        def __init__(self, path):
            seen["dir"] = path

    (tmp_path / "x.json").write_text("{}")
    monkeypatch.setattr(strata, "FhirStore", Store)
    monkeypatch.setattr(strata, "strata_counts", lambda truth: dict.fromkeys(ALL_STRATA, 0) | {ALL_STRATA[0]: 7})
    assert main(["--fhir-dir", str(tmp_path), "--as-of", "2026-01-02", "--min", "8"]) == 0
    assert seen["dir"] == tmp_path
    out = capsys.readouterr().out
    assert out.startswith(f"cohort {tmp_path}, as of 2026-01-02")
    assert f"under 8: 1 of the 1 situations in the cohort\n  {ALL_STRATA[0]} (7)" in out
