# Eval results

Scored by `evals/run.py`: the pipeline runs end to end (MCP server, PHI gateway, extract, assemble on a real
model behind the prompt guard, verify), the packet is rehydrated, and it is scored against ground truth read
from the raw Synthea bundles. Metrics are defined in `evals/metrics/citation_resolution.py`. Run output goes to
`evals/runs/` (gitignored); this file records what is worth keeping.

As of date pinned to 2026-09-20 (`AS_OF_DATE`).

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
| Tokens per run, in / out | 1,421 / 352 | 717 / 926 (thinking included) |
| Estimated cost per run | $0.0032 | $0.0040 |

Cost is estimated from token counts and current list prices, not read from an invoice. Flash sends about half the
input but writes about 2.6x the output, because it thinks; that is why it costs more per run than Haiku.

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

Not looked at: Gemini's `thinking_level`. Flash's cost is within 25% of Haiku's, so it is not urgent.

## Run 1: one patient, both providers

Patient `Loyd638 Auer97` (631bf667): RA established, methotrexate order **completed** (so the 90-day duration
cannot be shown and `dmard_trial` must be a gap), active disease established from the notes, no TB or
hepatitis B screening in the chart. Expected packet: evidence, gap, evidence, gap, gap.

| Provider | Model | citation_resolution_rate | citation_level | gap_accuracy | verify == truth | Tokens in / out |
|---|---|---|---|---|---|---|
| anthropic | claude-opus-5 | 1.00 | 1.00 | 1.00 | yes | 1960 / 579 |
| gemini | gemini-3.8-flash | 1.00 | 1.00 | 1.00 | yes | 732 / 517 |

Both models wrote the expected packet, including the trap: neither claimed 90 days of methotrexate from a
completed order.

**Correction (token counts).** The Gemini row's tokens (732 in / 517 out) leave out thinking tokens, which
Gemini reports separately and bills as output. On a real Flash call the split was 330 answer + 1,229 thinking
tokens, so recorded output and cost were understated (about 3.5x on that call; thinking varies run to run). The
client now counts them. Anthropic counts were not affected.

**Read this as a smoke test, not a score.** n = 1, one run each, and a case both models found easy. It shows the
runner works end to end and that nothing is broken; it cannot tell the providers apart. Telling them apart needs
the dataset (many patients, deliberately hard cases, repeated runs).
