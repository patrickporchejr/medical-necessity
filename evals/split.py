"""The held-out split: which cases tuning never reads. evals/held_out.txt is the split; this is
the method that wrote it, kept so the file can be checked against it.

    python evals/split.py            # sizes per situation and split; does the committed file match?
    python evals/split.py --write    # write evals/held_out.txt (only to create it; it is frozen)

Run from the repo root so the one top-level .env is found. The method is in HEADER below, which
is also the comment at the top of the file. Prints counts and 8-character case keys only.
"""

import argparse
import hashlib
import re
from datetime import date
from pathlib import Path

from app.config import settings
from dataset import GroundTruth, open_ground_truth
from experiment import HELD_OUT_FILE, load_held_out, patient_key, stratum_of

SEED = "held-out-v1"
SPLIT_MIN = 10  # a situation with fewer cases is not split: all of it stays in the working split
SHARE = 0.10  # of a split situation's cases go to held-out ...
FLOOR = 3  # ... but never fewer than this many
AS_OF = date(2026, 9, 20)  # the as of date the split was made at; strata depend on it
# Runs made before the split. Prompts and rules were tuned on them, so a case they ran is not
# held out. Named, not globbed, so a later run (held-out ones included) cannot change the split.
RESULTS_DIR = Path(__file__).parent / "results"
RUNS_BEFORE_SPLIT = ("run-0", "run-1", "run-2", "run-3")

HEADER = f"""\
# Held-out split: case keys (first 8 characters of the patient id) that tuning never reads.
# Frozen: G1 hand-labels these cases. evals/split.py wrote it and checks it; do not edit by hand.
# Made 2026-10-25 from the 228-patient cohort, strata as of {AS_OF.isoformat()}.
#
# Method, per situation (stratum_of in evals/experiment.py):
# - fewer than {SPLIT_MIN} cases: not split, every case stays in the working split. Held-out cases
#   from these would be one or two cases, too few to score, and would leave the working split
#   under 5. G1 builds these situations on purpose in its hard split (D16).
# - otherwise: max({FLOOR}, round({SHARE} * n)) cases go to held-out, drawn from the situation's
#   cases less two kinds that stay in the working split: the smallest and the largest chart (by
#   note count, ties by patient id), which the working selection always runs, and any case an
#   earlier run already ran ({", ".join(RUNS_BEFORE_SPLIT)} in evals/results; the old cohort shares
#   patients with this one). The rest are ordered by sha256("{SEED}:" + patient id) and the first
#   ones taken.
"""


def draw_order(patient_id: str) -> str:
    return hashlib.sha256(f"{SEED}:{patient_id}".encode()).hexdigest()


def held_out_count(n: int) -> int:
    return 0 if n < SPLIT_MIN else max(FLOOR, round(SHARE * n))


def seen_before_split() -> set[str]:
    """Every 8-hex-character key in the results of the runs made before the split."""
    return {key for run in RUNS_BEFORE_SPLIT for path in sorted((RESULTS_DIR / run).glob("*.json"))
            for key in re.findall(r"(?<![0-9a-f])[0-9a-f]{8}(?![0-9a-f])", path.read_text())}


def pick_held_out(groups: dict[str, list[tuple[int, str]]],
                  seen: set[str] = frozenset()) -> dict[str, list[str]]:
    """Per stratum, the held-out patient ids, from (note count, patient id) pairs. `seen` holds
    case keys already run, which are never held out."""
    picked = {}
    for stratum, members in sorted(groups.items()):
        ordered = sorted(members)
        extremes = {ordered[0][1], ordered[-1][1]}
        pool = sorted((pid for _, pid in ordered if pid not in extremes and patient_key(pid) not in seen),
                      key=draw_order)
        picked[stratum] = sorted(pool[:held_out_count(len(ordered))])
    return picked


def groups_of(truth: GroundTruth) -> dict[str, list[tuple[int, str]]]:
    groups: dict[str, list[tuple[int, str]]] = {}
    for case in truth.cases():
        groups.setdefault(stratum_of(truth, case.patient_id), []).append(
            (len(truth.store.chart(case.patient_id).documents), case.patient_id))
    return groups


def compute_held_out(truth: GroundTruth) -> list[str]:
    """The sorted held-out keys the method gives for this cohort."""
    picked = pick_held_out(groups_of(truth), seen_before_split())
    return sorted(patient_key(p) for ids in picked.values() for p in ids)


def file_text(keys: list[str]) -> str:
    return HEADER + "".join(f"{k}\n" for k in sorted(keys))


def report(truth: GroundTruth, held: set[str]) -> str:
    lines = [f"{'situation':<48}{'cases':>6}{'work':>6}{'held-out':>10}"]
    total = work = 0
    for stratum, members in sorted(groups_of(truth).items()):
        h = sum(patient_key(p) in held for _, p in members)
        lines.append(f"{stratum:<48}{len(members):>6}{len(members) - h:>6}{h:>10}"
                     + ("   not split" if len(members) < SPLIT_MIN else ""))
        total, work = total + len(members), work + len(members) - h
    lines.append(f"{'total':<48}{total:>6}{work:>6}{total - work:>10}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--write", action="store_true", help=f"write {HELD_OUT_FILE.name}")
    args = parser.parse_args(argv)
    if settings.as_of != AS_OF:
        print(f"note: AS_OF_DATE is {settings.as_of}; the split is computed as of {AS_OF}")
    truth = open_ground_truth(AS_OF)
    keys = compute_held_out(truth)
    if args.write:
        if HELD_OUT_FILE.exists():
            raise SystemExit(f"{HELD_OUT_FILE} exists and is frozen; delete it first to remake the split")
        HELD_OUT_FILE.write_text(file_text(keys))
        print(f"wrote {HELD_OUT_FILE}: {len(keys)} cases")
    committed = load_held_out() if HELD_OUT_FILE.exists() else []
    print(report(truth, set(committed or keys)))
    if not committed:
        print(f"\n{HELD_OUT_FILE.name} does not exist; --write creates it")
        return 1
    if committed != keys:
        print(f"\n{HELD_OUT_FILE.name} differs from what the method gives for this cohort")
        return 1
    print(f"\n{HELD_OUT_FILE.name} matches the method for this cohort")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
