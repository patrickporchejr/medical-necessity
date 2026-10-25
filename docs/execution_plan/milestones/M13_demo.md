# M13: the demo

**Source:** new (decisions D12–D14). **Agents:** demo, api, web, qa; you do the tool capture.
**Checkpoint:** [CP13](../HUMAN_QA.md#cp13-the-demo-outline). **Starts after CP7**, runs alongside M12. The
hosted demo needs CP12b; everything else can be built and captured locally.

Planned to the step; tasks written at CP7.

## Outcome

A demo that does not depend on you being in the room or on a model API being up:

1. **A demo mode in the product** (layer 2): reset, named scenarios, recorded model output, a guided tour, and
   two story paths.
2. **An interactive click-through** (layer 1), captured from the demo mode with Arcade (D14). It is the teaser:
   a short video or GIF for a LinkedIn post, linking to the clickable version, the hosted demo and the repo.
3. **An always-on hosted version**, password protected, running recorded mode by default (built with M12).
4. **A one-page solution brief**: the document a solutions architect would hand a hospital.

The audience is hiring managers for forward-deployed engineer and AI solutions architect roles, so the demo
is shaped like the two meetings those roles run: one with the business owner and one with the customer's
technical and security reviewers. Everywhere it is described as a proof of concept on synthetic data, with the
path to production stated, not as something ready for a hospital.

Demo mode is product code and is committed (D12). One-off recording helpers still stay local.

## Steps

| Step | Done when |
|---|---|
| Storyboard | `docs/demo/STORYBOARD.md` with two stories. **Buyer story** (about 2 minutes, for a health system's prior-auth lead): a worklist run, lanes, a records-arrive round trip, a signed packet, with time saved and the human sign-off as the takeaways. **Technical deep-dive** (about 3 minutes, for the customer's IT and security reviewers): the PHI boundary and sealed vault, a citation trace, `verify` catching an overclaim, the eval table, cost per case, and what happens when the lab is down. Every beat names the scenario and screen it needs. You approve it before anything else is built |
| Solution brief | `docs/demo/SOLUTION_BRIEF.md`, one page: the problem, the architecture, PHI handling (and why there is no BAA), the integration path to a real EHR over FHIR, eval results from RESULTS.md, cost per case, a rollout plan (shadow mode, then assisted review), and what is out of scope. Every number links to its source in the repo |
| Recorded mode | `llm_mode = "live" \| "recorded"`. A recording is one approved live run whose model responses are saved per (case key, node, criterion, attempt). They are keyed that way, not by prompt, because date offsets are random per run and prompts differ. Replay is deterministic and costs nothing. Recordings hold only de-identified model output |
| Scenarios | Named, resettable states: *overclaim caught* (a scripted model asserts an unsupported criterion and `verify` flags it), *records arrive* (a pending lab result released on request), *lab down*, *positive screen*. Each is reachable from a demo menu that only demo mode shows |
| Reset | One action returns the demo to the storyboard's starting state: cases, decisions, pending lab results, threads and sealed vaults (shredded and recreated) |
| Guided tour | An in-app overlay that walks each story, built with a small open-source library (license checked at the time). It can be turned off, respects reduced motion, and passes the WORKLIST_UI checklist |
| Click-through capture | You capture both stories in Arcade using the storyboard; the demo agent writes the step text and hotspots. Export a 60–90 second video or GIF of the buyer story for LinkedIn. Published links go in `docs/demo/` |
| Hosted demo | With M12: password gate, recorded mode by default, the live switch behind its own spend cap, reset on a schedule, and the monthly cost written down |

## The tool (D14): Arcade

Decided 2026-10-24. The demo is not part of a sales pipeline, so funnel analytics (Storylane's strength) would
go unused. What matters is how it lands on LinkedIn, where a post shows video or a GIF, not an interactive
embed, and a link to the clickable version. Arcade is built for that, and its free plan holds a few
demos. HTML capture is on paid tiers of every tool and is not needed. Check Arcade's free-plan limits again
before capture; sources disagree (Arcade's own guide says 3 published demos, reviews say fewer).
