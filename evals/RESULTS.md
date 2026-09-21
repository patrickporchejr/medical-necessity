# Eval results

Scored by `evals/run.py`: the pipeline runs end to end (MCP server, PHI gateway, extract, assemble on a real
model behind the prompt guard, verify), the packet is rehydrated, and it is scored against ground truth read
from the raw Synthea bundles. Metrics are defined in `evals/metrics/citation_resolution.py`. Run output goes to
`evals/runs/` (gitignored); this file records what is worth keeping.

As of date pinned to 2026-09-20 (`AS_OF_DATE`).

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
