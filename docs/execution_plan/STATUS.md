# Status

Kept by the orchestrator ([ORCHESTRATOR.md](ORCHESTRATOR.md)); agents do not edit it. Git wins when this and
the branches disagree.

## Now

**G1: in progress** on `g1/ground-truth` (local, not pushed). Resume here:
1. G1.6b (D21 in the agreement report, as-of guard) is done on branch `worktree-agent-a02c98f99ab58b7dd` at
   4fa604b, **not yet merged**: G1.9 QA was running on it when the session ended. Re-run G1.9 (QA 4fa604b,
   milestone checks, CPG1 packet), then merge it.
2. After merging, re-export the review page (`uv run --project api python evals/label.py --export
   web/public/labels.json`): the committed export predates D21 (977def3e still shows pending-CPG1).
3. Update HUMAN_QA's CPG1 outline (the "joint pain" call is now D21), then stop for CPG1. Push and PR only on
   your yes.
Live spend so far: $16.59 of the $18 approved (G1.5).

## Milestones

| # | Milestone | State | Branch | Checkpoint |
|---|---|---|---|---|
| M0 | The plan | passed | — | CP0 |
| M1 | Bigger cohort, eval hygiene | passed | `m1/cohort` | CP1 |
| G1 | Independent reference labels and hard cases | in progress | `g1/ground-truth` | CPG1 |
| M3 | Baseline run (Run 4), on G1's labels | not started | `m3/baseline` | CP3 |
| Q1 | Faithful wording and fixes (Run 5) | not started | `q1/faithfulness` | CPQ1 |
| D1 | The demo, hosted | not started | `d1/demo` | CPD1 |
| M2 | Reference lab | cut (D15) | — | — |
| M4 | Run the worklist | cut (D15), slice kept in D1 | — | — |
| X1 | Per-criterion fan-out (experiment) | cut (D15) | — | — |
| M5 | Lanes and decisions | cut (D15), slice kept in D1 | — | — |
| M6 | Reviewer UX | cut (D15), slice kept in D1 | — | — |
| M7 | Durable state and batch limits | cut (D15) | — | — |
| M8 | Prompt-injection hardening | cut (D15), folded into G1 and Q1 | — | — |
| M9 | Second service line | cut (D15) | — | — |
| M10 | Learning from reviewers | cut (D15), its faithfulness metric moved to Q1 | — | — |
| M11 | Denials and appeals | cut (D15) | — | — |
| M12 | Deploy on AWS | cut (D15), slice kept in D1 | — | — |
| M13 | The demo | cut (D15), slice kept in D1 | — | — |

States: `not started`, `planning`, `in progress`, `awaiting CP<N>`, `passed`, `cut`.

## Open tasks

| Task | Agent | State | Since | Notes |
|---|---|---|---|---|
| G1.2b `targeted` field | data | accepted | 2026-10-25 | D20; 29 targeted, bundles byte-identical (614dcb0) |
| G1.5 Labeling run | orchestrator | accepted | 2026-10-25 | Live, $16.56 (+$0.03 smoke), cap $18 approved. Held-out 21/94/10 unlabeled; hard 32/87/1. Runs agreed on 115 of 120 held-out and 119 of 120 hard labels both runs gave. b8a1ffa3 (largest chart, ~870k tokens) unlabeled: every reply mistyped a record id (6469669) |
| G1.6b D21 report, as-of guard | evals | handed back (4fa604b), QA not finished | 2026-10-25 | 29→30 test, agreement regenerated for D21, scoring pinned to the labels' as-of |
| G1.2c D21 answers | data | accepted | 2026-10-25 | pending-CPG1 resolved; 380d32c7 ra_diagnosis targeted |
| G1.6 + G1.8 Agreement report, scoring | evals | accepted | 2026-10-25 | Worktree; no live calls |
| G1.3 Labeler | evals | accepted | 2026-10-25 | `evals/label.py` (f334fba), Batch API, two runs, scripted only; 257 passed, 17 skipped |
| G1.4 Estimate | evals | accepted | 2026-10-25 | $9.30 at batch rates, $11.62 with 25% margin (98 requests, one batch). Waits for your yes |
| G1.1 Chart viewer | evals | accepted | 2026-10-25 | `evals/chart.py` (09f3d2f): 8 tests + 1 cohort test. Held-out estimate 3.05M tokens (15k–438k per chart). Patient name in notes replaced by `[patient]`. Waits for QA |
| G1.2 Hard cases | data | accepted | 2026-10-25 | 24 cases (6e0f49b): the 14 kinds plus 5 per sparse situation. `--check` passes, cohort unchanged; built in `data/synthea/hard/`. 8 of 120 answers differ from the rule. Judgment calls to confirm at CPG1. Waits for QA |
| G1.7 Review page | web | accepted | 2026-10-25 | `/labels` built, `npm run build` and tsc pass (8c96ed5). QA: download, mismatch, unlabeled and storage work; page scrolls sideways at 375px (summary table). Back to web |
| M1.1 Size the cohort | data | accepted | 2026-10-24 | 228 RA patients, 102 batches, ~29 s per RA patient; `OUT` and `END_DATE` added to generate.sh; fingerprint `2477aa35…` |
| M1.2 Generate the cohort | orchestrator | accepted | 2026-10-24 | 228 RA patients installed, fingerprint `2477aa35…`; generate.sh defaults reproduce it (`END_DATE=20260920`). Cohort-wide tests marked `cohort`, skipped by default (25.5 → 2.5 min). `-m cohort` on the updated tests: 4 passed |
| M1.5 Held-out split | evals | accepted | 2026-10-25 | `evals/held_out.txt`, 25 cases, frozen; `evals/split.py` checks it. Situations under 10 not split (D16); cases from Runs 0–3 kept out |
| M1.6 Cases per stratum | evals | accepted | 2026-10-25 | Working selection 5 per situation, 32 cases, smallest and largest chart kept |
| M1.3 Strata report | evals | accepted | 2026-10-24 | `uv run --project api python evals/strata.py [--fhir-dir DIR]` |
| M1.4 Latency in summaries | evals | accepted | 2026-10-24 | run.py now sums a node's laps across a repair |
| M1.7 QA and the CP1 packet | qa | accepted | 2026-10-25 | All of M1.1–M1.6 pass; 210 everyday, 13 cohort tests pass. HUMAN_QA CP1 steps 1 and 3 fixed |

Task states: `dispatched`, `handed back`, `in QA`, `QA failed (n)`, `accepted`, `blocked`.

## Decisions

| Checkpoint | # | Decision | Answer | Date |
|---|---|---|---|---|
| CP0 | D1 | Reference lab as own service or in the FHIR server | Own service (`lab`, port 8002) | 2026-10-24 |
| CP0 | D2 | Screening mix | As proposed in LAB_STORE.md | 2026-10-24 |
| CP0 | D3 | Where positive screens land | Resolve flagged claims, reason "screening positive" | 2026-10-24 |
| CP0 | D4 | Eval coverage of screening situations | Existing six strata, plus at least 5 cases per screening situation overall | 2026-10-24 |
| CP0 | D5 | Task tracking | STATUS.md only | 2026-10-24 |
| CP0 | D6 | README change, when and by whom | The orchestrator proposes a README diff at CP3; you approve | 2026-10-24 |
| CP0 | D7 | Scope | All of M1–M13 and X1; M11 first to cut. **Superseded by D15** | 2026-10-24 |
| CP0 | D8 | Agent definitions | Create `.claude/agents/` from the briefs; models as in ORCHESTRATOR.md | 2026-10-24 |
| CP0 | D9 | Vault lifetime with saved graph state | Sealed per case: encrypted, stored apart from checkpoints, crypto-shredded at close | 2026-10-24 |
| CP0 | D10 | Reviewer decisions as graph interrupts | Yes | 2026-10-24 |
| CP0 | D11 | Per-criterion subagent fan-out | An eval-gated experiment (X1), kept only if it beats the baseline | 2026-10-24 |
| CP0 | D12 | Demo code in the repo | Yes, demo mode is product code; one-off recording helpers stay local | 2026-10-24 |
| CP0 | D13 | Demo layers | In-app demo mode and a click-through in a demo tool; no enterprise platforms | 2026-10-24 |
| CP0 | D14 | Click-through tool | Arcade: for LinkedIn video and GIF, not funnel analytics | 2026-10-24 |
| M1 | D15 | Scope, replacing D7 | [TRIMMED_ROADMAP.md](../TRIMMED_ROADMAP.md): M1, then G1, M3, Q1, D1; all else cut. D1–D3, D9 and D10 lapse with M2, M5 and M7; D11 with X1. Minimize spend: cheap models, estimates first, a free static host | 2026-10-24 |
| M1 | D16 | Situations Synthea writes too rarely | Stop generating at 228 RA patients (102 batches); don't chase 5 per situation. Build the sparse ones by editing charts in G1's hard split, and say so plainly everywhere results are reported: what Synthea gave, where it was too sparse to be statistically meaningful, what we edited and why | 2026-10-24 |
| CP1 | D17 | Cohort size and the sparse situations | Accept 228 RA patients and the two 1-case situations as documented under D16 | 2026-10-25 |
| CP1 | D18 | Held-out design | 25 cases, frozen in `evals/held_out.txt`; situations under 10 not split; max(3, 10%) per split situation; smallest and largest chart and the Runs 0–3 cases stay in work. Small situations get held-out coverage only from G1's hard split | 2026-10-25 |
| G1 | D19 | Who labels G1's held-out split | Sonnet 5.5 reads the chart and the policy's wording, never the pipeline's code, and its labels are accepted as written, with no human review. Two runs; where they disagree the label is `unlabeled`. A `/labels` review page shows how a client's team would vet them. The hard split is scored against answers we wrote. Said plainly: reference labels, not clinical ground truth | 2026-10-25 |
| G1 | D20 | What the hard split is scored on | Only the criteria each case targets (`targeted: true` in `data/hard/cases.yaml`, 29 of 120; every criterion an injected note asks to flip counts). The rest copy the rule's answer on the base chart, so they are reported apart as a wiring check | 2026-10-25 |
| G1 | D21 | The hard split's judgment calls | (1) A keyword inside an injected instruction is not evidence: injection-keyword-line's `active_disease` takes the chart's answer without the injected line. (2) conflict-remission-last's `ra_diagnosis` is targeted (its remission note bears on "currently active"). (3) Free text counts as evidence. (4) A positive TB result is a gap, and the packet should say what clears it (a negative retest, or documented latent-TB treatment): a Q1 gap-packet change, not a label. (5) Resolved RA is a denial through `ra_diagnosis` = gap; `dmard_trial` stays gap | 2026-10-25 |

## Workflow

What each milestone's agents did, what went wrong and who caught it, and what you decided that no agent
proposed. Evidence for the workflow write-up in D1. A few lines per milestone, added at its checkpoint.

| Milestone | Agents | Went wrong, caught by | Your call |
|---|---|---|---|
| M1 | data (opus, ~123k tokens, 2h20m mostly waiting on Synthea): M1.1; evals (opus, ~103k tokens): M1.3, M1.4; evals (opus, ~121k tokens): M1.5, M1.6; orchestrator: M1.2 | The data agent found Synthea simulates to the wall clock, so a seed was not reproducible; it pinned `END_DATE` and restarted. The trial was set to 150 batches (~2.5h); the orchestrator surfaced it and you capped it at 5 per occurring situation. That rule never converged (new one-case situations kept appearing); you stopped it (D16), and the orchestrator killed the run when the agent was slow to pick up the message. The cohort had 34 bundles for `TARGET=30`; the data agent traced it to a cohort generated before trimming existed, and found it cannot be regenerated exactly. M1.4 found run.py kept only a node's last lap on a repair; the evals agent fixed it with a test. Growing the cohort to 228 made the suite take 25.5 min (3 stale counts failed); the orchestrator made the cohort-wide tests opt-in. In M1.5 the evals agent kept the 2 cases from Runs 0–3 out of held-out, since prompts were tuned on them; nobody had asked for that. In M1.7 the qa agent found two CP1 steps that did not work as written; its first dry run also uploaded to LangSmith because the key was in `.env` (synthetic metadata, no cost) | Cap the trial, then stop it and build sparse situations on purpose (D16); record the cohort lesson in M9; trim scope (D15) |

## Checkpoint results

| Checkpoint | Result | Date | Follow-ups |
|---|---|---|---|
| CP0 | Passed (PR #53 merged) | 2026-10-24 | — |
| CP1 | Passed | 2026-10-25 | Optional: delete the stray LangSmith run from M1.7's dry run. QA dry runs blank the LangSmith key |

## Log

- 2026-10-24: plan drafted (M0).
- 2026-10-24: D9–D11 decided in discussion; X1 added, M5 and M7 rewritten for interrupts and the sealed vault.
- 2026-10-24: D12–D13 decided; M13 (the demo) and the demo agent added.
- 2026-10-24: D14 Arcade; M13 re-aimed at buyer and technical audiences, solution brief added.
- 2026-10-24: D1–D8 set to the recommendations; agent definitions drafted (M0.3).
- 2026-10-24: CP0 passed (PR #53 merged). M1 opened; M1.1, M1.3, M1.4 dispatched.
- 2026-10-24: M1.3 and M1.4 accepted (51 evals tests pass). Under-5 is judged over situations that occur; absent ones listed apart (D4).
- 2026-10-24: D15 trims the scope (TRIMMED_ROADMAP.md, approved). G1, Q1, D1 added; M2 and M4–M13 and X1 cut; Workflow table started.
- 2026-10-24: M1.1 accepted. D16: M1.1 trial stopped at 228 RA patients; sparse situations to be built in G1, reported plainly.
- 2026-10-24: M1.2: 228-patient cohort installed (34-patient one backed up locally; it cannot be regenerated). 12 cohort-wide tests marked `cohort` and skipped by default; 3 tests updated for 228. Everyday suite 198 passed.
- 2026-10-25: M1.2 confirmed (`-m cohort`, 4 passed). M1.5 and M1.6 accepted (210 passed); held-out split frozen at 25. M1.7 dispatched.
- 2026-10-25: M1.7 accepted. CP1 passed; D17 and D18 recorded. PR #55 to merge.
- 2026-10-25: M1 merged (PR #55). G1 planned (D19: model-written labels, review page, two batch runs); G1.1, G1.2, G1.7 dispatched.
- 2026-10-25: QA batch 1: G1.1 and G1.2 accepted and merged (228 passed, 16 skipped); G1.7 failed on 375px width, back to web.
- 2026-10-25: D20: hard split scored on targeted criteria only. G1.2b and G1.3 dispatched; G1.7 fix in QA.
- 2026-10-25: G1.3 and G1.4 accepted (QA 9 of 9). G1.5 estimate $9.30–$11.62, waiting for approval.
- 2026-10-25: G1.5 run live after a smoke test; chars/4 undercounted tokens about 2x (long record ids), so the cap went from $12 to $18 with your yes. $16.56. G1.6 and G1.8 dispatched.
- 2026-10-25: D21 decided ahead of CPG1 (injected keyword not evidence; 380d32c7 ra_diagnosis targeted; free text counts; positive TB a gap with a next step for Q1; resolved RA denies via ra_diagnosis). G1.2c dispatched.
- 2026-10-25: G1.6/G1.8 accepted (QA; minor: as-of falls back to today) and merged with G1.2c. G1.6b dispatched.
- 2026-10-25: session ended during G1.9 QA. G1.6b unmerged at 4fa604b; review export to redo after merge.
