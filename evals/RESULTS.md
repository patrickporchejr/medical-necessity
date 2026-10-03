# Eval results

Scored by `evals/run.py`: the pipeline runs end to end (MCP server, PHI gateway, extract, assemble on a real
model behind the prompt guard, verify), the packet is rehydrated, and it is scored against ground truth read
from the raw Synthea bundles. Metrics are defined in `evals/metrics/citation_resolution.py`. Run output goes to
`evals/runs/` (gitignored); this file records what is worth keeping.

As of date pinned to 2026-09-20 (`AS_OF_DATE`).

**Where the numbers come from.** Every number below is read from a summary committed under
[`results/`](results/), one folder per run, linked where it is used. Raw runs stay in `evals/runs/` and are
not committed. Experiment summaries (`experiment_*.json`) are committed as written: they hold only case
keys, scores, model names, token counts and cost. Per-case run files (`<timestamp>_<key>.json`, from
`evals/run.py`) also carry the packet's text, so they are committed trimmed: the packet's `service` line and
each assertion's `text` are dropped and the patient id is cut to its 8-character key; criterion ids, kinds,
the placeholder ids cited, the verify and ground-truth flags, scores, tokens and per-node seconds are kept.
Each trimmed file names its source and what was dropped.

## Run 3: the final run, with the repair loop and the schema retry

Run 2026-10-03 with `python evals/experiment.py --repeat 3`, at commit `e256eaf` (the repair loop from MED-5, the
schema retry and the repair fallback from MED-6), clean tree. Same dataset, cases and as of date as Run 2: 11 cases
x 3 runs = 33 runs per model, 6 of them on the no-model `gap_packet` route. Each packet is scored twice: the model's
first draft (`first_pass_*`) and the final packet after any repair, so the loop cannot hide model quality.

| | claude-haiku-4-5 | gemini-3.8-flash |
|---|---|---|
| packet_fully_correct, first draft → final | 0.970 (32/33) → 1.000 | 1.000 → 1.000 |
| decisions_correct, first draft → final | 0.970 → 1.000 | 1.000 → 1.000 |
| citation_resolution_rate, first draft → final | 1.000 → 1.000 | 1.000 → 1.000 |
| gap_accuracy, first draft → final | 0.992 → 1.000 | 1.000 → 1.000 |
| Runs repaired | 1 of 33 | 0 |
| Repair calls that failed | 0 | 0 |
| Replies that needed the schema retry | 0 | 0 |
| verify agrees with ground truth | 33/33 | 33/33 |
| Run failures (error or refusal) | 0 | 0 |
| Tokens per run, in / out | 1,218 / 299 | 602 / 781 (thinking included) |
| Estimated cost per run (all 33, the 6 no-model runs at $0) | $0.0027 | $0.0034 |
| Estimated cost of the run | $0.09 | $0.11 |

Source: [`results/run-3/experiment_20261003T181114Z.json`](results/run-3/experiment_20261003T181114Z.json).

What it shows:
- **The repair loop fired once in 66 runs, and fixed what it was sent.** Haiku's one miss was a wrong call on a
  case with no methotrexate order and active-disease notes (`ra-active/no-order/active-notes · 875ecf6d`). verify
  flagged it, the criterion went back with verify's reasons, and the repaired packet was fully correct. Because the
  repair is told verify's verdict, the honest model number is the first-draft one: 0.970 for Haiku, 1.000 for Gemini.
- **Haiku's first drafts improved on Run 2** (0.909 → 0.970 fully correct, citation resolution 0.926 → 1.000) with
  no change to the extract or verify rules. Run 2's misses were ids not in the patient's chart; none recurred. With
  3 runs per case this is within what run-to-run variation could produce, so it is not claimed as an improvement.
- **The schema retry and the repair fallback never triggered.** Both are covered by tests against scripted models;
  on these two models every reply validated the first time.
- Same caveat as every run: ground truth shares `verify`'s rules, so "verify agrees with ground truth" checks the
  wiring, not whether the rules are right (the active-disease check ignores negation, for one).

## Run 2: the dataset, both cheap models, repeated

Run 2026-09-21 with `python evals/experiment.py --repeat 3`. As of date pinned to 2026-09-20 (`AS_OF_DATE`).
Dataset `prior-auth-adalimumab-ra` in LangSmith: 11 cases chosen by `select_patients` (the smallest and the largest
chart in each of the six situations the rules distinguish; one situation has a single patient), labels derived from
ground truth. 11 cases x 3 runs = 33 runs per model. Both models were the configured defaults.

| | claude-haiku-4-5 | gemini-3.8-flash |
|---|---|---|
| packet_fully_correct (right calls, every claim sound, nothing unaddressed) | 0.909 (30/33) | 1.000 (33/33) |
| decisions_correct (evidence vs gap on every criterion) | 0.970 (32/33) | 1.000 |
| citation_resolution_rate | 0.926 | 1.000 |
| gap_accuracy | 0.992 | 1.000 |
| ids_well_formed (no repair needed) | 1.000 | 1.000 |
| verify agrees with ground truth | 33/33 | 33/33 |
| Run failures (error or refusal) | 0 | 0 |
| Tokens per run, in / out | 1,420 / 352 | 717 / 926 (thinking included) |
| Estimated cost per run | $0.0032 | $0.0040 |
| Seconds per node, extract / assemble / verify | not in the summary (see below) | not in the summary |

Source: [`results/run-2/experiment_20260921T235938Z.json`](results/run-2/experiment_20260921T235938Z.json).
Rates are its `averages`; counts are the cases in its `cases` list; tokens and cost per run are its totals
divided by its 33 runs.

Cost is estimated from token counts and current list prices, not read from an invoice. Flash sends about half the
input but writes about 2.6x the output, because it thinks; that is why it costs more per run than Haiku.

**Latency.** The experiment summary keeps scores, tokens and cost but not the per-node `seconds` each run
records, so Run 2 has no latency figures on file. The nearest record is a single `evals/run.py` run of both
models on `631bf667` the same evening, 25 minutes before Run 2's summary was written
([`results/run-2/20260921T233432Z_631bf667.json`](results/run-2/20260921T233432Z_631bf667.json)): Haiku
0.84 / 3.19 / 0.67 s and Flash 0.58 / 3.20 / 0.45 s for extract / assemble / verify. That is one run each on
one case, not a Run 2 measurement; to measure Run 2, the summary has to carry `seconds`.

**What Haiku got wrong: 3 of 33 runs, two kinds of mistake, both caught by `verify`.**
- Two runs (cases `c106918a` and `d1b49867`, both "RA active, no order, no active-disease notes") made the right call on
  `ra_diagnosis` but cited `<CONDITION_1>`, an id that is not in that patient's chart. The claim was flagged as
  unsupported, so nothing wrong reached a reviewer unmarked.
- One run (`875ecf6d`, "RA active, no order, active notes") wrote a gap for `active_disease` although the chart
  establishes it. A false gap. `verify` flagged it.

In every one of the 66 runs, `verify` and the offline ground truth agreed on every assertion, so the runtime check
is doing its job independently of the model.

**How much to read into this.** 33 runs per model, 11 cases, and only 3 misses, clustered in the "no order"
situations, each of which has two cases. Flash is ahead on this dataset but the gap is 3 runs; that is a lead worth
following, not a ranking. The dataset needs more cases per situation before the models can be separated: see #27.
The cheap run to try next is Haiku alone at `--repeat 5` on the "no order" cases, to see whether the wrong-id and
false-gap misses repeat or were one-offs.

Not looked at: Gemini's `thinking_level`. Flash's cost is about a quarter above Haiku's
($0.1323 against $0.1049 for the 33 runs), so it is not urgent.

## Run 1: one patient, both providers

Patient `Loyd638 Auer97` (631bf667): RA established, methotrexate order **completed** (so the 90-day duration
cannot be shown and `dmard_trial` must be a gap), active disease established from the notes, no TB or
hepatitis B screening in the chart. Expected packet: evidence, gap, evidence, gap, gap.

| Provider | Model | citation_resolution_rate | citation_level | gap_accuracy | verify == truth | Tokens in / out | Seconds: extract / assemble / verify |
|---|---|---|---|---|---|---|---|
| anthropic | claude-opus-5 | 1.00 | 1.00 | 1.00 | yes | 1960 / 579 | 0.73 / 7.31 / 0.36 |
| gemini | gemini-3.8-flash | 1.00 | 1.00 | 1.00 | yes | 732 / 517 | 0.42 / 3.17 / 0.38 |

Source: [`results/run-1/20260920T235054Z_631bf667.json`](results/run-1/20260920T235054Z_631bf667.json)
(trimmed). Seconds are the `seconds` the run recorded per graph node. There is one run per model, so each is a
single measurement, not a mean; `assemble` (the model call) is most of the time for both.

Both models wrote the expected packet, including the trap: neither claimed 90 days of methotrexate from a
completed order.

**Correction (token counts).** The Gemini row's tokens (732 in / 517 out) leave out thinking tokens, which
Gemini reports separately and bills as output. On a real Flash call the split was 330 answer + 1,229 thinking
tokens, so recorded output and cost were understated (about 3.5x on that call; thinking varies run to run). The
client now counts them. Anthropic counts were not affected. (The 330 / 1,229 split comes from a one-off call
outside any recorded run, so no committed summary backs it.)

**Read this as a smoke test, not a score.** n = 1, one run each, and a case both models found easy. It shows the
runner works end to end and that nothing is broken; it cannot tell the providers apart. Telling them apart needs
the dataset (many patients, deliberately hard cases, repeated runs).

## Run 0: the first dataset run, and the bracket repair

Run 2026-09-21 00:16 UTC, after Run 1, with `python evals/experiment.py --repeat 3` on four models: the earliest
dataset run on file. Its labels and metrics predate Run 2's (no `ids_well_formed` yet, and the case names lack the
`ra-active` part of the situation), so it is not comparable to Run 2 except on the one metric this section is
about. Source: [`results/run-0/experiment_20260921T001642Z.json`](results/run-0/experiment_20260921T001642Z.json).

| | claude-opus-5 | claude-sonnet-5 | claude-haiku-4-5 | gemini-3.8-flash |
|---|---|---|---|---|
| citation_resolution_rate | 1.00 (27 runs cited anything) | 0.09 (3/33) | 0.00 (0/33) | 0.85 (28/33) |
| decisions_correct | 27/33 (labels wrong, see below) | 33/33 | 33/33 | 33/33 |
| packet_fully_correct | 27/33 | 3/33 | 0/33 | 28/33 |
| verify agrees with ground truth | 33/33 | 33/33 | 33/33 | 33/33 |

**What broke.** Haiku and Sonnet made the right call on every criterion in every run, yet almost none of their
citations resolved. The pattern (right decisions, unresolvable ids) pointed at the ids, not at the models'
judgment. One run of each on `631bf667` showed it: both cited `CONDITION_1` and `DOCUMENTREFERENCE_27` to
`_31` where the chart has `<CONDITION_1>` and `<DOCUMENTREFERENCE_27>`, and `verify` rejected each one as "not
found in this patient's chart"
([Sonnet](results/run-0/20260921T001756Z_631bf667.json), [Haiku](results/run-0/20260921T001807Z_631bf667.json)).
The angle brackets are part of the placeholder id, and the models had dropped them. Flash's 5 runs scoring 0.0 look
like the same failure, but the experiment summary does not record the ids, so that is not confirmed. `verify`
agreed with ground truth in all 132 runs: the dropped brackets were caught, not passed through.

**The fix.** A repair, a prompt rule, and a metric to watch it, all in commit 31ca575:
- `repair_citations` (`api/app/graph/nodes/assemble.py`) restores the brackets on an id that is a bare
  placeholder (`CONDITION_1` becomes `<CONDITION_1>`) and counts every repair. Anything else stays as written,
  so a wrong id is still flagged by `verify`.
- The assemble prompt now says record ids look like `<CONDITION_3>`, the brackets are part of the id, and to
  copy them character for character. It also says criterion ids are plain (`ra_diagnosis`, never wrapped in
  brackets): one Sonnet run in between bracketed the criterion ids as well, so all five assertions were "unknown
  criterion" and every criterion went unaddressed
  ([run](results/run-0/20260921T002224Z_631bf667.json)).
- The count became the `ids_well_formed` metric: true when a run needed no repair. A repaired id resolves, so it
  is a reliability signal, not a truthfulness one.

**Before and after (citation_resolution_rate).**

| | claude-sonnet-5 | claude-haiku-4-5 | gemini-3.8-flash | Source |
|---|---|---|---|---|
| Run 0, dataset, 33 runs | 0.09 | 0.00 | 0.85 | [Run 0](results/run-0/experiment_20260921T001642Z.json) |
| Before the fix, `631bf667`, 1 run | 0.00 | 0.00 | | [Sonnet](results/run-0/20260921T001756Z_631bf667.json), [Haiku](results/run-0/20260921T001807Z_631bf667.json) |
| After the fix, `631bf667` | 1.00 (1 run) | 1.00 (2 runs) | | [Sonnet](results/run-0/20260921T002316Z_631bf667.json), [Haiku](results/run-0/20260921T002238Z_631bf667.json), [Haiku](results/run-0/20260921T002330Z_631bf667.json) |
| After the fix, 3 cases, 1 run each | | 1.00 | 1.00 | [check](results/run-0/experiment_20260921T004302Z.json) |
| Run 2, dataset, 33 runs | | 0.926 | 1.000 | [Run 2](results/run-2/experiment_20260921T235938Z.json) |
| Run 2 ids_well_formed | | 1.000 | 1.000 | [Run 2](results/run-2/experiment_20260921T235938Z.json) |

In every run after the fix, `citations_repaired` was 0: with the prompt rule, the models wrote the brackets
themselves, and the repair was not needed. In Run 2, `ids_well_formed` was 1.000 for both models (33/33 each),
so the repair did not touch a single id; Haiku's remaining citation misses are ids that are well formed but not
in that patient's chart (see Run 2), which no repair should fix. The repair stays as a backstop. Sonnet and Opus
were not rerun on the dataset after the fix.

**Opus's 6 wrong decisions were our labels.** All six are the two cases `7aefca51` and `9d05650c` (gap_accuracy
0.8 on each run: one criterion of five called against the label). Their RA diagnosis is resolved, not active;
per commit 31ca575 the model read that correctly and the label did not, which is why `ra_diagnosis` now
requires an active diagnosis and why Run 2 files those two cases under `ra-not-active`.
