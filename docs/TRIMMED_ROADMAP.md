# Trimmed roadmap

Status: proposal, revised 2026-10-24. Replaces decision D7 ("All of M1–M13 and X1") in
[execution_plan/STATUS.md](execution_plan/STATUS.md) once you approve it. [ROADMAP.md](ROADMAP.md) and the
milestone files stay as written; this page says which of them still run, in what order, and what is new.

## What the project is for

A portfolio piece for forward-deployed engineer and AI solutions architect roles, built alongside a full-time
job, to show what that job does not. It answers three questions a hiring manager asks:

1. **Can you build an AI system that is credible?** Every claim traced to a source, every limit written down.
2. **Can you show it to a customer?** A demo a stranger can open, shaped like the meetings those roles run.
3. **How do you use AI?** The repo is built by an orchestrator and subagents working from a written plan, with
   contracts before code and human checkpoints that block progress. The plan, the decision log and the
   checkpoint results are public.

The third answer depends on the first. The obvious follow-up to "agents built most of this" is "how do you
know it's right?", and the answer is the evals. If the evals are circular, the workflow story falls apart under
that question. Independent labels and hard cases are what let you say "I direct agents, and I verify their
output against ground truth they didn't write."

The roadmap's own rule applies: "Add a node, agent or dependency only when an eval or a real constraint
demands it." Nothing here goes to production, so anything that serves only production is cut.

## Why trim

The full plan (15 milestones) spends most of its effort on a worklist product: lanes, a reviewer UX, sealed
vaults, an improver, appeals. Meanwhile two weaknesses wait until M10 or are never fixed:

1. **Ground truth shares the pipeline's rules.** `evals/dataset.py` imports `criterion_supports` and
   `find_excerpt_lines` from the pipeline, so 1.000 on Run 3 checks wiring, not correctness. RESULTS.md says
   so; the fix is labels that do not come from the code.
2. **Nothing checks that an assertion's wording is faithful to the record it cites.** A citation can resolve
   and still be described wrongly.

The trimmed plan fixes those first, then ships a demo, a thin worklist included, that a stranger can open
without Docker. It has no feedback from people who do prior authorization for a living; the write-up says so,
as a limit, rather than implying the packet's format was validated with them.

## What runs

| # | Milestone | From | Spend | Checkpoint |
|---|---|---|---|---|
| M1 | Bigger cohort, eval hygiene | unchanged, in progress on `m1/cohort` | none | CP1 |
| G1 | Independent reference labels and hard cases | **new** | none | CPG1 |
| M3 | Baseline run (Run 4), on G1's labels | M3, changed | yes, approve first | CP3 |
| Q1 | Faithful wording, and the fixes Run 4 calls for (Run 5) | **new**, takes the metric from M10 | yes, approve first | CPQ1 |
| D1 | The demo, hosted, with a thin worklist and the workflow write-up | M4–M6, M12 and M13, cut down | none (static host) | CPD1 |

After CPD1 the README goes back to "complete as a demo", with the new numbers.

Running across all of them: **workflow tracking** and **minimal spend**. See below.

## Spend

A personal project, so spend is kept as low as the work allows. There are three kinds:

- **Model calls in eval runs.** Small already: Run 3 cost $0.09 (Haiku) and $0.11 (Gemini Flash) for 33 runs
  each. Run 4 and Run 5 use the same two models. Each comes with an estimate from the case count before it
  runs, and runs only on your yes. Repeats (`--repeat 3`) run on the held-out and hard splits only; the
  working split runs once. No LLM judge unless you approve its cost per run.
- **Hosting.** The demo's monthly cost should be zero. Recorded mode needs no API and no model, so the
  dashboard can be exported as a static site with the recorded output bundled in, and served from a free
  static host. D1 checks this first; containers only if the static export cannot work, and then the cheapest
  host, with the monthly cost stated before you approve it.
- **Agent usage** (your Claude plan). One agent at a time unless two tasks are truly independent; Sonnet for
  mechanical tasks (QA runs, labeling tooling, doc updates), Opus where judgment matters; no polling loops
  while a long job runs.

## Cut or deferred

| Milestone | Decision | Why |
|---|---|---|
| M2 Reference lab | Cut | Turns the TB and hepatitis B gaps into real lookups, but it is a second data source, server and schema change for two criteria. G1 plants screening results in the hard split instead, so those criteria are tested at all |
| M4–M6 Worklist, lanes, reviewer UX | Cut to a thin slice in D1 | The queue and lanes are worth showing; the cost is in M5's design (decisions resume the graph, the vault outlives the run, M7 makes it durable). D1 keeps the screens and drops that machinery |
| M7 Durable state, sealed vault | Cut | Needed only by graph-resuming decisions and appeals. Keeps the README's "the vault is never written to disk" true |
| X1 Per-criterion fan-out | Cut | Optimizes latency and repair tokens on a run that costs $0.003 and fires one repair in 66 |
| M10 Learning from reviewers | Cut; its faithfulness metric moves to Q1 | One reviewer's decisions on synthetic cases are not enough data to learn from |
| M11 Denials and appeals | Cut | Already the first to cut |
| M12 Deploy on AWS | Cut to D1's hosting | Recorded mode makes no model calls, so a static export on a free host is enough |
| M8 Prompt-injection hardening | Folded into G1 and Q1 | Injected notes join the hard split, Run 4 measures them, Q1 fixes what got through, and the cases run in CI. Its lab-comment cases needed M2, and its token caps protect live calls the recorded demo does not make; the paid runs are already gated by your approval |
| M9 Second service line | Cut | Shows the design generalizes, but it is a second cohort, a second policy and a second labeling pass. The cohort lesson stays in its milestone file for whoever picks it up |

The cut milestone files stay in the repo, marked `cut` in STATUS.md, so the thinking is not lost.

## Workflow tracking (from now)

So the workflow write-up in D1 has evidence, not recollection. At each checkpoint, the orchestrator adds a
short entry to a "Workflow" table in STATUS.md:

- what each agent was dispatched to do, and roughly what it cost (tokens, or the session's usage if that is
  all there is);
- what an agent got wrong, and who caught it: another agent, a test, the orchestrator, or you at the
  checkpoint;
- what you decided at the checkpoint that the agents had not proposed.

A few lines per milestone. M1's entry can be written from what has already happened (the 150-batch cap, the
34-versus-30 bundle question, the M9 cohort lesson).

## G1: independent reference labels and hard cases

Changed by D19: a model writes the held-out labels and nobody reviews them. The milestone file is
[G1_reference_labels.md](execution_plan/milestones/G1_reference_labels.md).

**Outcome:** labels for the held-out split that do not come from the pipeline's code, and a set of hard cases
the current rules are expected to get wrong.

- **A chart viewer.** A command that prints one case's chart readably: conditions, medication requests,
  observations and notes, with record ids and dates, and nothing from a packet or the ground truth.
- **Model labels.** Sonnet 5.5 reads each held-out chart and the policy's wording (no codes, no keywords, none
  of the pipeline's prompts) and records evidence or gap per criterion, with record ids and a one-line reason.
  One paid run, approved first. The labels are accepted as written: nobody with prior-auth experience reviews
  them, and the project's author does not either. The write-up says so plainly.
- **A review page.** A `/labels` screen where a client's team would vet the model's labels (agree, disagree,
  unsure). It is built to show the workflow and is not filled in for G1.
- **Agreement report.** Model labels against computed ground truth on held-out, and against our answers on the
  hard split (which measures the labeler). Every disagreement is a rule finding, a suspected labeler error or
  a real ambiguity; the label is not edited.
- **Hard cases.** Synthetic notes and records added to copies of cases on purpose, each with an expected
  outcome written when the case is built:
  - negation: "denies joint pain", "no synovitis on exam"
  - conflicting notes: an active-disease note followed by a remission note
  - dates the chart cannot settle: a methotrexate order with no end date and a later note that mentions it
  - a criterion met only in free text, not by a coded record
  - screening results: a negative TB result, a negative hepatitis B result, and a **positive** one
  - prompt injection (the core of M8), with the same expected outcome as without the injection
  - the situations Synthea writes too rarely (D16), built up to enough cases to score

  They go in their own split (`hard`), reported apart, and are scored against our answers, not the
  labeler's. The write-up says that we wrote these cases and their answers, so the hard split is independent
  of the code, not of us.
- **Scoring.** Held-out against the model labels, hard against our answers, counts beside rates. The working
  split keeps computed ground truth as a wiring check.
- **No fixes in G1.** The rules stay as they are, so M3 measures them honestly.

**Done when:** model labels and hard cases are committed, the review page renders them, the agreement report
runs, and every disagreement is explained in its output.

- **Synthea's coverage, said plainly (D16).** Wherever results are reported (RESULTS.md, the demo, the brief,
  the workflow write-up), say which situations Synthea produced naturally and in what numbers, which were too
  sparse to say anything statistically about, and which cases we edited to fill them, and why. Generating
  more was tried: the rare situations stayed rare, and new ones kept appearing with one case each.

## M3: baseline run (Run 4)

M3 as written, plus G1's splits: the paid run reports the working, held-out and hard splits apart, the
held-out split against G1's model labels and the hard split against our answers, with counts. Expect lower numbers than Run 3, especially on the
hard split. The write-up says that this is the point: Run 3 measured agreement with the rules, Run 4 measures
agreement with labels the code did not write.

## Q1: faithful wording, and the fixes Run 4 calls for (Run 5)

**Outcome:** a check that each evidence assertion says what its cited record says, the rule fixes Run 4
pointed at, and a re-run that shows what changed.

- **Faithfulness, deterministically first.** Require `assemble` to quote the span of the cited record that
  supports each assertion, and have `verify` check the quote appears in that record (after de-identification,
  on the placeholder text). This needs no LLM judge, so it keeps the cost decision behind closed issue #21.
- **Its limit, written down.** A quote can be real and still not support the claim: "no synovitis on exam" is
  a real span, cited as evidence of active disease. The quote check catches invented wording, not misread
  wording; the negation hard cases and the rule fixes cover the rest. Measure what the quote check catches on
  the hard split before considering a judge; if a judge is proposed, it comes with its cost per run and needs
  your yes.
- **Rule fixes.** Fix what Run 4 found, starting with negation in the active-disease check and the value of a
  screening result. Each fix is justified by a case that failed, and scored against G1's labels, never
  against labels computed from the rule being fixed. If an injection got through in Run 4, it is fixed here.
- **Injection cases in CI.** The injection cases run with scripted models in the existing `tests` workflow,
  so a regression is caught without spend.
- **Run 5.** Same splits and models as Run 4. Report first-draft and final, per split, with counts. A change
  counts as an improvement only if it is larger than the run-to-run variation Run 4 showed and is more than one
  or two cases.

**Done when:** Run 5 is in RESULTS.md beside Run 4, with every rule change linked to the case that motivated
it, and the held-out split not used to tune.

## D1: the demo, hosted

**Outcome:** a stranger can see the product work from a link, without Docker or a model key; can see it as a
worklist, not only one case at a time; and can read how it was built.

- **Recorded mode** (from M13): the dashboard replays recorded model output for a fixed set of cases, so the
  hosted version makes no model calls and costs nothing per visit. A banner says it is recorded and synthetic.
- **A thin worklist** (from M4–M6):
  - the cases table, with search and the summary per case (M4.2, M4.3), and one control that runs the
    recorded cohort (no pause, resume or concurrency measurement);
  - the lane function and lane counts with a filter (M5.1, M5.2);
  - the case in the pane, with Next in queue within a lane (M6 step 6);
  - decision buttons that record the decision and its note in the viewer's session, and move the case to
    resolved. They do not resume the graph, and the page says so;
  - usable at desktop and at 375px.
- **The Arcade click-through**, captured from recorded mode.
- **The one-page solution brief**, for the business owner and the technical and security reviewer.
- **The workflow write-up**, `docs/HOW_THIS_WAS_BUILT.md`: the orchestrator, the agents and the checkpoints
  in one page, then one milestone traced end to end (dispatch, handoff, checkpoint) with what went wrong and
  who caught it, drawn from the workflow table in STATUS.md. It answers "how do you know it's right?" by
  pointing at G1, Run 4 and Run 5.
- **Re-recorded videos**, if the product changed enough since the current two.
- **Hosting:** a static export of the dashboard with the recorded output bundled in, on a free static host
  (see Spend). Containers on the cheapest host only if the export cannot work, with the cost approved first.
  With no live model calls there is no spend cap or passcode to build. A live-model switch is out of scope.

Cut from M13: the guided tour, named scenarios beyond the recorded cases, the two story paths.

**Done when:** the hosted link, the click-through, the brief and the workflow write-up are in the README, and
the monthly cost of leaving the demo up is a number in the repo.

## Order

1. Finish M1.
2. G1.
3. M3 (Run 4).
4. Q1 (Run 5), using what Run 4 found.
5. D1, then the README update.

That is the whole plan. Anything else needs an eval or a real constraint to bring it back.

## Applying this

For the orchestrator, once you approve this page:

- Log a decision in STATUS.md that replaces D7 with this plan, dated.
- Mark M2, M4, M5, M6, M7, M8, M9, M10, M11, M12, M13 and X1 as `cut` (M8 with "folded into G1 and Q1"; M4–M6, M12 and
  M13 with "slice kept in D1").
- Add G1, Q1 and D1 to the milestone table, and write their milestone files under
  `docs/execution_plan/milestones/` at the checkpoint before each, as the plan already does.
- Add the Workflow table to STATUS.md, with M1's entry.
- Update M3's milestone file for the changes above.
- Update the order and "Why this order" section of `execution_plan/README.md`.
- Add CPG1, CPQ1 and CPD1 to `HUMAN_QA.md`.
- M1 continues on `m1/cohort` unchanged.
