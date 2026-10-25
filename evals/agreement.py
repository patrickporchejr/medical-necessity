"""How far the reference labels, the rule and the hard split's answers agree (G1.6).

    uv run --project api python evals/agreement.py            # print the report
    uv run --project api python evals/agreement.py --write    # and write evals/labels/agreement.md
    uv run --project api python evals/agreement.py --check    # fail if agreement.md is out of date

Run from the repo root so the one top-level .env is found. No model is called.

Three answers exist per case and criterion:

- model: the labeler's label (evals/labels/held_out.yaml, hard.yaml), `evidence`, `gap` or `unlabeled`.
  Labels are read, never edited.
- computed: the rule's answer over the whole chart (dataset.GroundTruth.established), what the working
  split is scored against.
- answer: on the hard split only, the answer written when the case was built (data/hard/cases.yaml).

What is compared, each with counts beside rates:

- held-out: model against computed, per criterion and overall. Unlabeled labels are counted apart, by why:
  the two runs gave different labels, or a run gave no valid reply.
- hard, targeted criteria (D20): model against answer, per kind. This measures the labeler. `pending-CPG1`
  is counted apart. Separately, computed against answer: where the rule and the answers we wrote differ.
- hard, untargeted criteria: model against answer and computed against answer, as a wiring check (these
  answers are the rule's on the base chart by construction).
- run to run: of the labels both runs gave, how many the two runs gave alike.

Every disagreement is listed with one classification line (rule finding, suspected labeler error or real
ambiguity) from evals/labels/classifications.yaml, keyed by split, case, criterion and side (`model` when the
labeler's label differs from the reference, `computed` when the rule differs from the hard split's answer).
The command fails if a disagreement has no classification, or a classification no disagreement.
"""

import argparse
import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable, Iterable

import yaml

from app.graph.nodes.extract import ONGOING
from chart import chart_records, estimate_tokens, find_patient, format_chart
from dataset import HARD_DIR, GroundTruth, open_ground_truth

EVALS = Path(__file__).resolve().parent
ROOT = EVALS.parent
LABELS_DIR = EVALS / "labels"
CASES_FILE = ROOT / "data" / "hard" / "cases.yaml"
CLASSIFICATIONS_FILE = LABELS_DIR / "classifications.yaml"
REPORT_FILE = LABELS_DIR / "agreement.md"
PROMPT_FILE = EVALS / "labeler_prompt.md"

STATUSES = ("evidence", "gap")
PENDING = "pending-CPG1"
CLASSES = ("rule finding", "suspected labeler error", "real ambiguity")
RUNS_DISAGREED, NO_VALID_REPLY = "runs disagreed", "no valid reply"
RA_CODE, MTX_CODE, CAUSE_OF_DEATH = "69896004", "105585", "69453-9"


# --- what is compared ---------------------------------------------------------------------------------

@dataclass(frozen=True)
class Item:
    """One case and criterion, with every answer we have for it."""
    split: str  # "held_out" or "hard"
    key: str
    criterion: str
    model: str  # "evidence", "gap" or "unlabeled"
    unlabeled: str | None  # why, when model is "unlabeled"
    reason: str  # the labeler's one line
    computed: str  # "evidence" or "gap"
    answer: str | None = None  # hard only: cases.yaml's expected
    targeted: bool | None = None  # hard only (D20)
    kind: str | None = None  # hard only
    name: str | None = None  # hard only

    def value(self, side: str) -> str | None:
        return getattr(self, side)


def unlabeled_why(label: dict[str, Any]) -> str | None:
    """Why a label is unlabeled: both runs labeled it differently, or a run gave no valid reply."""
    if label["status"] in STATUSES:
        return None
    runs = label.get("runs") or []
    return RUNS_DISAGREED if runs and all(r["status"] in STATUSES for r in runs) else NO_VALID_REPLY


def build_items(held_out: dict, hard: dict, cases: list[dict],
                computed: Callable[[str, str, str], str]) -> list[Item]:
    """`held_out` and `hard` are the label files as read; `computed(split, key, criterion)` is the rule's answer."""
    items = []
    for key, labels in held_out["cases"].items():
        for cid, label in labels.items():
            items.append(Item("held_out", key, cid, label["status"], unlabeled_why(label), label["reason"],
                              computed("held_out", key, cid)))
    for case in cases:
        labels = hard["cases"][case["key"]]
        for cid, spec in case["criteria"].items():
            label = labels[cid]
            items.append(Item("hard", case["key"], cid, label["status"], unlabeled_why(label), label["reason"],
                              computed("hard", case["key"], cid), spec["expected"], bool(spec["targeted"]),
                              case["kind"], case["name"]))
    return items


@dataclass(frozen=True)
class Comparison:
    name: str
    split: str
    side: str  # "model" or "computed"
    reference: str  # "computed" or "answer"
    targeted: bool | None = None  # hard: which criteria

    def items(self, items: Iterable[Item]) -> list[Item]:
        return [i for i in items if i.split == self.split
                and (self.targeted is None or i.targeted == self.targeted)]


COMPARISONS = (
    Comparison("held-out: model vs computed", "held_out", "model", "computed"),
    Comparison("hard, targeted: model vs answer", "hard", "model", "answer", True),
    Comparison("hard, targeted: computed vs answer", "hard", "computed", "answer", True),
    Comparison("hard, untargeted (wiring): model vs answer", "hard", "model", "answer", False),
    Comparison("hard, untargeted (wiring): computed vs answer", "hard", "computed", "answer", False),
)


@dataclass
class Tally:
    agree: int = 0
    compared: int = 0
    pending: int = 0
    unlabeled: Counter = None  # why -> count

    def __post_init__(self):
        self.unlabeled = self.unlabeled or Counter()

    def __str__(self) -> str:
        return of(self.agree, self.compared)


def tally(items: Iterable[Item], side: str, reference: str) -> tuple[Tally, list[Item]]:
    """Agreement of `side` with `reference`, and the items where they differ. Pending answers and unlabeled
    model labels are counted apart, never as agreement or disagreement."""
    t, differ = Tally(), []
    for item in items:
        ref = item.value(reference)
        if ref == PENDING:
            t.pending += 1
            continue
        if side == "model" and item.model == "unlabeled":
            t.unlabeled[item.unlabeled] += 1
            continue
        t.compared += 1
        if item.value(side) == ref:
            t.agree += 1
        else:
            differ.append(item)
    return t, differ


def of(n: int, total: int) -> str:
    return f"{n} of {total}" + (f" ({n / total:.0%})" if total else "")


# --- run to run ---------------------------------------------------------------------------------------

def run_pair(label: dict[str, Any]) -> str:
    """"agreed", "disagreed", or "no pair" when a run gave no label."""
    if label["status"] in STATUSES:
        return "agreed"
    return "disagreed" if unlabeled_why(label) == RUNS_DISAGREED else "no pair"


def run_to_run(labels: dict) -> dict[str, Counter]:
    """Per criterion (and "all"), how the two runs compared."""
    out: dict[str, Counter] = {"all": Counter()}
    for case in labels["cases"].values():
        for cid, label in case.items():
            outcome = run_pair(label)
            out.setdefault(cid, Counter())[outcome] += 1
            out["all"][outcome] += 1
    return out


# --- classifications ----------------------------------------------------------------------------------

def disagreement_keys(items: list[Item]) -> dict[tuple[str, str, str, str], tuple[Comparison, Item]]:
    """(split, key, criterion, side) -> the comparison and item, for every disagreement in COMPARISONS."""
    out = {}
    for comparison in COMPARISONS:
        _, differ = tally(comparison.items(items), comparison.side, comparison.reference)
        for item in differ:
            out[(item.split, item.key, item.criterion, comparison.side)] = (comparison, item)
    return out


def flatten(classifications: dict) -> dict[tuple[str, str, str, str], dict[str, str]]:
    return {(split, key, cid, side): entry
            for split, cases in (classifications or {}).items()
            for key, criteria in (cases or {}).items()
            for cid, sides in criteria.items()
            for side, entry in sides.items()}


def check_classifications(keys: Iterable[tuple], classifications: dict) -> dict[tuple, dict[str, str]]:
    """Every disagreement classified, nothing classified that is not one, and every class one of CLASSES."""
    flat, wanted = flatten(classifications), set(keys)
    problems = [f"no classification for {'/'.join(k)}" for k in sorted(wanted - set(flat))]
    problems += [f"classified but not a disagreement: {'/'.join(k)}" for k in sorted(set(flat) - wanted)]
    problems += [f"{'/'.join(k)}: class must be one of {', '.join(CLASSES)}, got {e.get('class')!r}"
                 for k, e in sorted(flat.items()) if e.get("class") not in CLASSES or not e.get("why")]
    if problems:
        raise SystemExit(f"{CLASSIFICATIONS_FILE.name}:\n  " + "\n  ".join(problems))
    return flat


# --- known patterns, counted from the charts -----------------------------------------------------------

def _ra_onset(chart) -> str | None:
    dates = [c.onset_date[:10] for c in chart.conditions if c.code.code == RA_CODE and c.onset_date]
    return min(dates) if dates else None


def patterns(cohort: GroundTruth, items: list[Item], held_out: dict) -> dict[str, Any]:
    """The numbers the report's "known patterns" section quotes, all computed here."""
    from app.graph.support import criterion_supports
    active = next(c for c in cohort.criteria.criteria if c.id == "active_disease")
    by = {(i.split, i.key, i.criterion): i for i in items}
    out: dict[str, Any] = {}

    # Resolved RA (D20): the rule says dmard_trial is gap because the order is not ongoing; the answers say
    # gap because the RA resolved, so no inadequate response can be documented.
    resolved = [p for p in cohort.store.patient_ids() if not cohort.established(p, "ra_diagnosis")]
    with_notes = 0
    for p in resolved:
        chart = cohort.store.chart(p)
        orders = [m for m in chart.medication_requests if m.code.code == MTX_CODE]
        named = any(re.search("methotrexate", t, re.I) for t in chart.document_text.values())
        with_notes += bool(orders) and all(m.status not in ONGOING for m in orders) and named
    out["resolved"] = {"cohort": len(resolved), "patients": len(cohort.store.patient_ids()),
                       "active_notes": sum(cohort.established(p, "active_disease") for p in resolved),
                       "completed_order_named_in_notes": with_notes}
    for split in ("held_out", "hard"):
        pool = [i for i in items if i.split == split and i.criterion == "dmard_trial"
                and by[(split, i.key, "ra_diagnosis")].computed == "gap"]
        out["resolved"][split] = {"charts": len(pool), "model": Counter(i.model for i in pool),
                                  "computed": Counter(i.computed for i in pool),
                                  "answer": Counter(i.answer for i in pool if i.answer)}

    # Deceased: a cause-of-death record, and an RA Condition still coded active.
    dead = [p for p in cohort.store.patient_ids()
            if any(o.code.code == CAUSE_OF_DEATH for o in cohort.store.chart(p).observations)]
    held_keys = set(held_out["cases"])
    out["deceased"] = {"cohort": len(dead),
                       "ra_evidence": sum(cohort.established(p, "ra_diagnosis") for p in dead),
                       "held_out": [(p[:8], by[("held_out", p[:8], "ra_diagnosis")].model)
                                    for p in sorted(dead) if p[:8] in held_keys
                                    and by[("held_out", p[:8], "ra_diagnosis")].computed == "evidence"]}

    # The templated chief complaint: notes the rule matches for active_disease, dated before the diagnosis.
    rows = []
    for key in held_out["cases"]:
        item = by[("held_out", key, "active_disease")]
        if item.computed != "evidence":
            continue
        chart = cohort.store.chart(find_patient(cohort.store, key))
        onset = _ra_onset(chart)
        matched = [d for d in chart.documents
                   if criterion_supports(active, "DocumentReference", None, chart.document_text[d.id])]
        early = [d for d in matched if onset and d.date and d.date[:10] < onset]
        rows.append({"key": key, "notes": len(chart.documents), "matched": len(matched),
                     "before_diagnosis": len(early), "model": item.model})
    out["template"] = {"charts": len(rows), "all_notes": sum(r["matched"] == r["notes"] for r in rows),
                       "before_diagnosis": sum(r["before_diagnosis"] > 0 for r in rows),
                       "model": Counter(r["model"] for r in rows)}

    # The unlabeled long chart: which ids the replies typed, against the ids the chart has.
    no_reply = sorted({i.key for i in items if i.split == "held_out" and i.unlabeled == NO_VALID_REPLY})
    out["no_reply"] = []
    for key in no_reply:
        label = held_out["cases"][key]
        typed = sorted({m for cid in label for r in label[cid].get("runs") or []
                        for m in re.findall(r"(?:Condition|MedicationRequest|Observation|DocumentReference)/"
                                            r"[0-9a-f-]+", r["reason"])})
        records = chart_records(cohort.store.chart(find_patient(cohort.store, key)), key)
        refs = {r["ref"] for s in ("conditions", "medication_requests", "observations", "notes") for r in records[s]}
        ra = sorted(r["ref"] for r in records["conditions"] if r["code"] and r["code"].endswith(RA_CODE))
        out["no_reply"].append({"key": key, "typed": typed, "real_ra": ra, "typed_exist": sum(t in refs for t in typed),
                                "tokens_estimate": estimate_tokens(format_chart(records)),
                                "records": len(refs), "notes": len(records["notes"])})
    return out


# --- the report ---------------------------------------------------------------------------------------

def _counts(c: Counter, order: Iterable[str]) -> str:
    return ", ".join(f"{k} {c[k]}" for k in order if c[k]) or "none"


def report(items: list[Item], held_out: dict, hard: dict, flat: dict, numbers: dict) -> str:
    criteria = list(dict.fromkeys(i.criterion for i in items))
    L: list[str] = []
    add = L.append
    add("# Agreement report (G1.6)")
    add("")
    add("Written by `uv run --project api python evals/agreement.py --write`; do not edit by hand. The numbers are "
        "computed from the label files, `data/hard/cases.yaml` and the charts; the classification lines come from "
        "`evals/labels/classifications.yaml`. Labels are never edited.")
    add("")
    add(f"Labeler `{held_out['labeler']}` (prompt sha256 `{held_out['prompt_sha256'][:12]}…`, two runs), labeled "
        f"{held_out['date']}, judged as of {held_out['as_of']}. Held-out: {len(held_out['cases'])} cases; hard: "
        f"{len(hard['cases'])} cases. Each count is of case × criterion labels. These are reference labels written "
        "by a model, not clinical ground truth (D19).")
    add("")

    # Headline.
    add("## Headline")
    add("")
    add("| Comparison | Agree | Counted apart |")
    add("|---|---|---|")
    for comp in COMPARISONS:
        t, _ = tally(comp.items(items), comp.side, comp.reference)
        apart = []
        if t.unlabeled:
            apart.append(f"unlabeled {sum(t.unlabeled.values())} ({_counts(t.unlabeled, (RUNS_DISAGREED, NO_VALID_REPLY))})")
        if t.pending:
            apart.append(f"{PENDING} {t.pending}")
        add(f"| {comp.name} | {t} | {'; '.join(apart) or '—'} |")
    for split, labels in (("held-out", held_out), ("hard", hard)):
        r = run_to_run(labels)["all"]
        both = r["agreed"] + r["disagreed"]
        add(f"| run to run, {split}: the two runs gave the same label | {of(r['agreed'], both)} | "
            f"a run gave no label: {r['no pair']} |")
    add("")
    add("The hard split's targeted rows measure the labeler against answers we wrote. The untargeted rows are a "
        "wiring check: those answers are the rule's on the base chart by construction (D20), so computed vs answer "
        "there should be all agreement, and model vs answer there repeats the held-out comparison on other charts.")
    add("")

    # Held-out per criterion.
    add("## Held-out: model vs computed, per criterion")
    add("")
    add("| Criterion | Agree | Model gap, computed evidence | Model evidence, computed gap | Unlabeled: runs disagreed | Unlabeled: no valid reply |")
    add("|---|---|---|---|---|---|")
    for cid in [*criteria, None]:
        pool = [i for i in items if i.split == "held_out" and (cid is None or i.criterion == cid)]
        t, differ = tally(pool, "model", "computed")
        add(f"| {cid or '**all**'} | {t} | {sum(i.model == 'gap' for i in differ)} | "
            f"{sum(i.model == 'evidence' for i in differ)} | {t.unlabeled[RUNS_DISAGREED]} | {t.unlabeled[NO_VALID_REPLY]} |")
    add("")

    # Hard per kind.
    add("## Hard, targeted criteria, per kind")
    add("")
    add("| Kind | Targeted | Model vs answer | Computed vs answer | Pending | Unlabeled |")
    add("|---|---|---|---|---|---|")
    kinds = list(dict.fromkeys(i.kind for i in items if i.split == "hard"))
    for kind in [*kinds, None]:
        pool = [i for i in items if i.split == "hard" and i.targeted and (kind is None or i.kind == kind)]
        tm, _ = tally(pool, "model", "answer")
        tc, _ = tally(pool, "computed", "answer")
        add(f"| {kind or '**all**'} | {len(pool)} | {tm} | {tc} | {tm.pending} | {sum(tm.unlabeled.values())} |")
    add("")
    add("## Hard, untargeted criteria (wiring check), per criterion")
    add("")
    add("| Criterion | Untargeted | Model vs answer | Computed vs answer | Unlabeled |")
    add("|---|---|---|---|---|")
    for cid in [*criteria, None]:
        pool = [i for i in items if i.split == "hard" and not i.targeted and (cid is None or i.criterion == cid)]
        tm, _ = tally(pool, "model", "answer")
        tc, _ = tally(pool, "computed", "answer")
        add(f"| {cid or '**all**'} | {len(pool)} | {tm} | {tc} | {sum(tm.unlabeled.values())} |")
    add("")

    # Run to run.
    add("## Run to run, per criterion")
    add("")
    add("Of the labels both runs gave, how many the two runs gave alike. Where they differ the label is "
        "`unlabeled` (D19); where a run gave no valid reply there is no pair to compare.")
    add("")
    add("| Criterion | Held-out | Held-out, no pair | Hard | Hard, no pair |")
    add("|---|---|---|---|---|")
    rh, rk = run_to_run(held_out), run_to_run(hard)
    for cid in [*criteria, "all"]:
        a, b = rh.get(cid, Counter()), rk.get(cid, Counter())
        add(f"| {'**all**' if cid == 'all' else cid} | {of(a['agreed'], a['agreed'] + a['disagreed'])} | {a['no pair']} | "
            f"{of(b['agreed'], b['agreed'] + b['disagreed'])} | {b['no pair']} |")
    add("")

    # Every disagreement.
    add("## Every disagreement, classified")
    add("")
    add("Classes: **rule finding** (the rule's answer is wrong or reads the policy more loosely than its wording), "
        "**suspected labeler error** (the label does not follow the chart or the policy's wording), **real "
        "ambiguity** (the chart and the wording allow both answers). Judged by reading each chart with "
        "`evals/chart.py`. The label stays as written either way.")
    add("")
    tallied: Counter = Counter()
    by_comparison: dict[str, Counter] = {}
    for comp in COMPARISONS:
        _, differ = tally(comp.items(items), comp.side, comp.reference)
        if not differ:
            continue
        add(f"### {comp.name} ({len(differ)})")
        add("")
        for item in differ:
            entry = flat[(item.split, item.key, item.criterion, comp.side)]
            tallied[entry["class"]] += 1
            by_comparison.setdefault(comp.name, Counter())[entry["class"]] += 1
            where = f"{item.key}" + (f" ({item.name}, {item.kind})" if item.name else "")
            sides = f"model {item.model}, computed {item.computed}" + (f", answer {item.answer}" if item.answer else "")
            add(f"- **{where}, `{item.criterion}`**: {sides}.")
            add(f"  - Model's reason: {item.reason}")
            add(f"  - **{entry['class']}**: {' '.join(entry['why'].split())}")
        add("")

    add("## Classification tally")
    add("")
    add("| Comparison | " + " | ".join(CLASSES) + " | Total |")
    add("|---|" + "---|" * (len(CLASSES) + 1))
    for name, c in by_comparison.items():
        add(f"| {name} | " + " | ".join(str(c[k]) for k in CLASSES) + f" | {sum(c.values())} |")
    add("| **all** | " + " | ".join(str(tallied[k]) for k in CLASSES) + f" | {sum(tallied.values())} |")
    add("")

    # Known patterns.
    n = numbers
    add("## Known patterns")
    add("")
    tpl = n["template"]
    add(f"- **The templated chief complaint.** Synthea writes the patient's symptom list as a `# Chief Complaint` "
        f"block in every note, decades before the diagnosis. In {of(tpl['before_diagnosis'], tpl['charts'])} held-out "
        f"charts where the rule finds `active_disease`, notes the rule matches are dated before the RA diagnosis, "
        f"and in {tpl['all_notes']} of them every note matches. The rule matches a keyword in any note at any date "
        f"and never checks \"despite therapy\". On these charts the labeler said: {_counts(tpl['model'], ('evidence', 'gap', 'unlabeled'))}.")
    res = n["resolved"]
    add(f"- **Resolved RA and `dmard_trial` (D20).** {res['cohort']} of {res['patients']} cohort charts have no "
        f"active RA diagnosis under the rule, the natural resolved-RA charts ({res['cohort'] - res['active_notes']} "
        f"without active notes, {res['active_notes']} with); in {res['completed_order_named_in_notes']} of them every "
        "methotrexate order is completed yet notes still name methotrexate. The rule calls "
        f"`dmard_trial` a gap there because no order is ongoing; the hard split's answers call it a gap because "
        f"the RA resolved, so no inadequate response is documented. Both say gap for different reasons, so "
        f"agreement on these charts does not test whether the rule reads \"inadequate response\" (it does not: "
        f"see the rule findings above). Held-out resolved-RA charts: {res['held_out']['charts']}, labeler "
        f"{_counts(res['held_out']['model'], ('evidence', 'gap', 'unlabeled'))}; hard: {res['hard']['charts']}, "
        f"labeler {_counts(res['hard']['model'], ('evidence', 'gap', 'unlabeled'))}, answers "
        f"{_counts(res['hard']['answer'], ('evidence', 'gap'))}.")
    dead = n["deceased"]
    add(f"- **Deceased patients.** {dead['cohort']} of {res['patients']} cohort charts carry a cause-of-death "
        f"record; in {dead['ra_evidence']} of them the rule still finds a currently active RA diagnosis, because the "
        "RA Condition stays coded active after death. Held-out charts like that, with the labeler's "
        f"`ra_diagnosis`: {', '.join(f'{k} {m}' for k, m in dead['held_out']) or 'none'}. Where the labeler also "
        "says evidence, the two agree on an answer both should have doubted, so agreement there is not "
        "confirmation.")
    for r in n["no_reply"]:
        add(f"- **{r['key']} is unlabeled because every reply mistyped a record id.** It is the largest held-out "
            f"chart ({r['notes']} notes, {r['records']} records, about {r['tokens_estimate']:,} tokens by "
            "characters / 4, which undercounted about 2x in G1.5, so roughly 870k billed). In both runs, on the "
            "first try and the retry, the reply cited the RA Condition by an id that is not in the chart: "
            f"{', '.join(f'`{t}`' for t in r['typed'])} ({r['typed_exist']} of {len(r['typed'])} exist), where the "
            f"chart's is {', '.join(f'`{t}`' for t in r['real_ra'])}. All four replies were rejected by the check "
            "that every cited id is in the chart, so all five criteria are unlabeled. This is a finding about long "
            "charts and long ids: copying a 36-character id exactly out of an ~870k-token prompt failed 4 times in 4. "
            "The pipeline's model cites short placeholders (`<CONDITION_1>`) instead of real ids, so it is not "
            "exposed in the same way, but a long chart is still where a model is most likely to slip.")
    add("- **Hard agreement is partly built in.** The labeler's prompt (`evals/labeler_prompt.md`) states the reading "
        "conventions: a criterion mentioned only to deny or rule out is a gap, a later record can change what an "
        "earlier one shows, and one the chart cannot settle is a gap. The hard split's answers follow the same "
        "conventions (negation, conflict and undetermined-date cases), so the labeler's agreement there is "
        "partly agreement with its own instructions, not independent confirmation.")
    add("")
    add("## Not measured")
    add("")
    add("- Whether the labels are clinically right: no human reviewed them (D19). The `/labels` page shows how a "
        "client's team would.")
    add("- Whether the cited records match: only the status (`evidence` or `gap`) is compared, not the records.")
    add("- The labeler and the pipeline's model are both Claude models and may share blind spots; the hard split's "
        "targeted answers are the guard. The splits are small: a difference of one or two labels is not a finding.")
    return "\n".join(L) + "\n"


# --- running ------------------------------------------------------------------------------------------

def load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text())


def computed_answers(cohort: GroundTruth, hard: GroundTruth) -> Callable[[str, str, str], str]:
    ids: dict[tuple[str, str], str] = {}

    def answer(split: str, key: str, cid: str) -> str:
        truth = cohort if split == "held_out" else hard
        if (split, key) not in ids:
            ids[(split, key)] = find_patient(truth.store, key)
        return "evidence" if truth.established(ids[(split, key)], cid) else "gap"

    return answer


def build(labels_dir: Path = LABELS_DIR, cases_file: Path = CASES_FILE,
          classifications_file: Path = CLASSIFICATIONS_FILE, hard_dir: Path = HARD_DIR) -> str:
    held_out, hard = load_yaml(labels_dir / "held_out.yaml"), load_yaml(labels_dir / "hard.yaml")
    if held_out["as_of"] != hard["as_of"]:
        raise SystemExit("the two label files were judged at different dates")
    as_of = date.fromisoformat(str(held_out["as_of"]))
    cohort, hard_truth = open_ground_truth(as_of), open_ground_truth(as_of, hard_dir)
    cases = load_yaml(cases_file)["cases"]
    items = build_items(held_out, hard, cases, computed_answers(cohort, hard_truth))
    flat = check_classifications(disagreement_keys(items), load_yaml(classifications_file))
    return report(items, held_out, hard, flat, patterns(cohort, items, held_out))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help=f"also write {REPORT_FILE.relative_to(ROOT)}")
    mode.add_argument("--check", action="store_true", help=f"fail if {REPORT_FILE.relative_to(ROOT)} is out of date")
    args = parser.parse_args(argv)
    text = build()
    if args.check:
        if not REPORT_FILE.exists() or REPORT_FILE.read_text() != text:
            print(f"{REPORT_FILE.relative_to(ROOT)} is out of date; run with --write", file=sys.stderr)
            return 1
        print(f"{REPORT_FILE.relative_to(ROOT)} is up to date")
        return 0
    sys.stdout.write(text)
    if args.write:
        REPORT_FILE.write_text(text)
        print(f"\nwrote {REPORT_FILE.relative_to(ROOT)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
