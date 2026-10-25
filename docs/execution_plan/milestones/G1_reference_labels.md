# G1: independent reference labels and hard cases

**Source:** [TRIMMED_ROADMAP.md](../../TRIMMED_ROADMAP.md#g1-independent-reference-labels-and-hard-cases), as
changed by D19. **Agents:** evals, data, web, qa. **Checkpoint:** [CPG1](../HUMAN_QA.md#cpg1-independent-reference-labels-and-hard-cases-outline).
**Live model spend: one labeling run, which needs your approval first.**

## Outcome

Labels for the held-out split that do not come from the pipeline's code, and a hard split whose answers we
wrote on purpose. M3 then scores against both instead of against `criterion_supports`, so Run 4 measures
something the pipeline did not compute for itself.

The held-out labels are written by a model (Sonnet 5.5) that reads each chart and the policy's wording. It does
not see the pipeline's code, prompts, keywords, codes or packets. Nobody with clinical or prior-auth experience
reviews them, and you do not review them either (D19). The labels are accepted as the labeler wrote them. A
review page is built to show how a client's team would vet them, but no one fills it in for G1.

## Tasks

| Task | Agent | Depends on | Done when |
|---|---|---|---|
| G1.1 Chart viewer | evals | — | `uv run --project api python evals/chart.py <key>` prints one chart: conditions, medication requests, observations and every note, with record ids, dates and status. No patient demographics, nothing from packets or ground truth, and no keyword filtering. `--json` prints the same for G1.3 and G1.7. `--sizes` prints the token count per held-out chart (input for G1.4) |
| G1.2 Hard cases | data, evals | — | A seeded, committed post-step edits copies of generated charts into a `hard` split (list below). Each case's expected outcome per criterion is in `data/hard/cases.yaml`, written when the case is built, with a one-line reason. The cohort's own bundles are not changed |
| G1.3 Labeler | evals | G1.1 | `evals/label.py` with its own prompt (nothing imported from `api/app/llm/prompts` or `app.graph`). For each case and criterion it gets the criterion `description` and the service line, with no codes or keywords, plus the G1.1 chart. It returns `evidence` or `gap`, the record ids, and a one-line reason. Requests go through the Message Batches API (half price; labeling needs no fast reply), with prompts checked by the same PHI guard as the pipeline's. Every cited id must exist in the chart, and a reply that fails is retried once and then recorded as `unlabeled`. Tested with scripted models only |
| G1.4 Estimate | evals | G1.1, G1.2, G1.3 | Tokens per chart × cases (held-out and hard) × 2 runs × the Sonnet 5.5 batch price (half of list), plus a margin, in the handoff. The orchestrator asks you before G1.5 |
| G1.5 Labeling run | evals | your yes | The labeler runs on held-out and hard. `evals/labels/held_out.yaml` and the hard-split model labels are committed with the model id, prompt hash and date. Labels are not edited after the run |
| G1.6 Agreement report | evals | G1.5 | A command lists, with counts: model labels against computed ground truth on held-out, and model labels against `data/hard/cases.yaml` on hard. The second one measures the labeler. Every disagreement gets a line saying it is a rule finding, a suspected labeler error or a real ambiguity. The label stays as written either way |
| G1.7 Review page | web | G1.1, G1.3 (format only) | A `/labels` route in `web/`. Per case and criterion it shows the label, its reason and the cited records' text, with agree, disagree and unsure buttons and a note. It reads a committed JSON export (no API call), so it survives D1's static export. Decisions stay in browser storage and download as JSON. It shows "not reviewed" by default. Works on the scripted labels before G1.5 |
| G1.8 Scoring | evals | G1.5 | `evals/experiment.py` scores held-out against the model labels and hard against `data/hard/cases.yaml` on its targeted criteria (D20), reporting counts beside rates ("19 of 20"). The working split keeps computed ground truth as a wiring check |
| G1.9 QA and packet | qa | all | Full suite passes; the review page builds and renders the real labels; the agreement report runs; no rule in `api/app/graph/` changed; CPG1 packet assembled |

G1.1, G1.2 and G1.7's layout run in parallel. G1.3 waits for the viewer, and G1.5 waits for your yes.

## Hard split

About 15 cases, each a copy of a generated chart with records or notes added:

- negation: "denies joint pain", "no synovitis on exam"
- conflicting notes: an active-disease note followed by a remission note
- dates the chart cannot settle: a methotrexate order with no end date and a later note that mentions it
- a criterion met only in free text, not by a coded record
- screening results: a negative TB result, a negative hepatitis B result, and a **positive** one (expected to
  fail; nothing in the rules checks the value)
- prompt injection (folded M8): notes that carry instructions, some on keyword-matching lines. The expected
  outcome is the same as without the injection
- the situations Synthea writes too rarely (D16), built up to enough cases to score

The hard split is scored against our answers, not the labeler's, because the labeler reads the same injected
notes as the pipeline.

## Contracts

- **Hard split, data side (G1.2).** `data/hard/build.py` (seeded, committed) copies base charts from
  `data/synthea/fhir/` into `data/synthea/hard/` (gitignored, like the cohort), editing each one. It never
  changes `data/synthea/fhir/`. Base charts come from outside `evals/held_out.txt`. Each copy gets a new
  patient id, `uuid5(NAMESPACE_URL, "hard/" + case name)`, so its 8-character key differs from its base.
  `data/hard/cases.yaml` is the spec and the answers: per case, `name`, `kind` (negation, conflict,
  undetermined-date, free-text-only, screening, injection, sparse-situation), `base` (base case key), `key`,
  `edits` (what was added, by record id), and per criterion `expected` (`evidence` or `gap`) with `records`,
  a one-line `reason` and `targeted` (true when the case's edits are about that criterion; false when the
  answer is the base chart's under the current rule). The hard split is scored on targeted criteria only
  (D20); the rest are reported apart as a wiring check. D21 settled the judgment calls (a keyword inside an
  injected instruction is not evidence), so 30 criteria are targeted. Dates are judged at the cohort's as-of date, 2026-09-20.
- **Label files (G1.3, G1.5).** `evals/labels/held_out.yaml` and `evals/labels/hard.yaml` hold the model's
  labels: per case key, per criterion: `status` (`evidence`, `gap` or `unlabeled`), `records` (list of
  `Type/id`), `reason` (one line). At the top: `labeler` (model id), `prompt_sha256`, `date`, `runs: 2`. The
  evals agent owns the format.
- **Review page export (G1.3 writes, G1.7 reads).** `web/public/labels.json`, written by an evals command and
  committed. It is the only file evals writes under `web/`. Shape:

  ```json
  {"labeler": "claude-sonnet-5-5", "date": "2026-10-25", "scripted": true,
   "criteria": [{"id": "ra_diagnosis", "description": "..."}],
   "cases": [{"key": "1a2b3c4d", "split": "held_out",
     "labels": [{"criterion": "ra_diagnosis", "status": "evidence", "reason": "...",
       "expected": null,
       "records": [{"ref": "Condition/abc", "date": "2019-03-02", "summary": "Rheumatoid arthritis (active)",
                    "text": "excerpt, notes only, or null"}]}]}]}
  ```

  `expected` is the by-construction answer on the hard split and `null` on held-out. Until G1.5, the web agent
  builds against `web/public/labels.sample.json`, a scripted fixture of 3 cases in this shape that it writes.
  Review decisions are kept in browser storage under `labels-review-v1` and download as
  `{"case", "criterion", "verdict": "agree"|"disagree"|"unsure", "note"}` rows.
- The labeler's prompt lives in `evals/`, apart from the pipeline's prompts, and its hash is recorded with the
  labels.
- No rule changes in G1. The rules stay as they are, so M3 measures them honestly.

## Caveats, as they will be written up

- Reference labels, not clinical ground truth. A model wrote them, and no human reviewed them.
- The labeler is an LLM, so it can share the pipeline's blind spots, such as negation or injected text.
  The hard split, scored against answers we wrote, guards against that. The G1.6 report shows how often the
  labeler itself got the hard split right.
- In a real engagement the client's reviewers would vet the labels on the review page, and ground truth for
  production would be human-labeled.
- Synthea's coverage (D16): which situations it produced naturally and in what numbers, which were too sparse
  to say anything statistical about, and which cases we built to fill them.

## Labeler runs

Two runs (D19). Where the two disagree, the label is `unlabeled` and left out of scoring, with the count
reported, so the labeler's consistency is a number, not a guess.

## Risks

- Sonnet and Haiku are in the same family and may make the same mistakes. The write-up says so. The hard
  split is the guard against it.
- Long charts. The held-out split has about 3,000 notes. If a chart is too long for one call, the labeler
  labels one criterion per call. G1.4's estimate covers whichever is chosen.
- The hard split's judgment calls were settled ahead of CPG1 (D21); you confirm them at the checkpoint.
- The splits are small. A difference of one or two cases is not a finding.
