"""Cases per eval situation (stratum) in a cohort: is it big enough to sample from?

    python evals/strata.py                                   # the current cohort, data/synthea/fhir
    python evals/strata.py --fhir-dir /path/to/trial/fhir    # a trial cohort
    python evals/strata.py --as-of 2026-09-20 --min 5

Run from the repo root so the one top-level .env is found. A situation is what `stratum_of` in
experiment.py names: RA active or not, DMARD trial shown / ordered but not shown / no order, and
active-disease notes or not. The report gives cases per situation the cohort has and the total,
names those with fewer than `--min` cases, and lists the possible situations the cohort lacks
(the dataset samples only situations that occur, so those are reported apart). The duration rules depend on the as of date, so pin it
(`--as-of` or AS_OF_DATE) to compare cohorts. Prints counts only, never a patient's name or id.
"""

import argparse
from datetime import date
from itertools import product
from pathlib import Path

from app.config import settings
from app.graph.criteria import load_criteria
from app.mcp.store import FhirStore
from dataset import CRITERIA_FILE, GroundTruth
from experiment import stratum_of

MIN_PER_STRATUM = 5
# Every value stratum_of can return, in its sort order, so an empty situation shows as 0.
ALL_STRATA = sorted(
    "/".join(parts) for parts in product(
        ("ra-active", "ra-not-active"), ("no-order", "trial-not-shown", "trial-shown"),
        ("active-notes", "no-active-notes"),
    )
)


def strata_counts(truth: GroundTruth) -> dict[str, int]:
    """Cases per situation, every possible one included (0 when the cohort has none)."""
    counts = dict.fromkeys(ALL_STRATA, 0)
    for case in truth.cases():
        stratum = stratum_of(truth, case.patient_id)
        if stratum not in counts:
            raise ValueError(f"stratum_of returned {stratum!r}, which ALL_STRATA does not list")
        counts[stratum] += 1
    return counts


def under(counts: dict[str, int], minimum: int) -> list[str]:
    """Situations the cohort has, but fewer than `minimum` times. Ones it lacks are not here:
    the dataset (select_patients) samples only the situations that occur."""
    return [s for s, n in counts.items() if 0 < n < minimum]


def absent(counts: dict[str, int]) -> list[str]:
    return [s for s, n in counts.items() if n == 0]


def format_report(counts: dict[str, int], *, fhir_dir: Path, as_of: date, minimum: int) -> str:
    width = max(map(len, counts))
    present = {s: n for s, n in counts.items() if n}
    lines = [f"cohort {fhir_dir}, as of {as_of}", ""]
    lines += [f"  {stratum:<{width}}  {n:>4}" for stratum, n in present.items()]
    lines += [f"  {'total':<{width}}  {sum(counts.values()):>4}", ""]
    short = under(counts, minimum)
    lines.append(f"under {minimum}: {len(short)} of the {len(present)} situations in the cohort"
                 + "".join(f"\n  {s} ({counts[s]})" for s in short))
    missing = absent(counts)
    lines.append(f"not in the cohort: {len(missing)} of {len(counts)} possible"
                 + "".join(f"\n  {s}" for s in missing))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--fhir-dir", type=Path, default=settings.fhir_dir,
                        help=f"a directory of Synthea patient bundles (default {settings.fhir_dir})")
    parser.add_argument("--as-of", type=date.fromisoformat, default=settings.as_of,
                        help="YYYY-MM-DD; default AS_OF_DATE, else today")
    parser.add_argument("--min", type=int, default=MIN_PER_STRATUM, dest="minimum",
                        help=f"a situation with fewer cases is reported as under (default {MIN_PER_STRATUM})")
    args = parser.parse_args(argv)
    if not any(args.fhir_dir.glob("*.json")):
        raise SystemExit(f"no patient bundles (*.json) in {args.fhir_dir}")

    truth = GroundTruth(FhirStore(args.fhir_dir), load_criteria(CRITERIA_FILE), args.as_of)
    counts = strata_counts(truth)
    print(format_report(counts, fhir_dir=args.fhir_dir, as_of=args.as_of, minimum=args.minimum))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
