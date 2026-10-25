# M13: the demo

**Source:** new (decisions D12–D14). **Agents:** demo, api, web, qa; you do the tool capture.
**Checkpoint:** [CP13](../HUMAN_QA.md#cp13-the-demo-outline). **Starts after CP7**, runs alongside M12. The
hosted demo needs CP12b; everything else can be built and captured locally.

Planned to the step; tasks written at CP7.

## Outcome

A demo that does not depend on you being in the room or on a model API being up:

1. **A demo mode in the product** (layer 2): reset, named scenarios, recorded model output, a guided tour, and
   two story paths.
2. **An interactive click-through** (layer 1), captured from the demo mode with Arcade, Supademo or Storylane
   (D14), shareable as a link or embed, with view analytics.
3. **An always-on hosted version**, password protected, running recorded mode by default (built with M12).

Demo mode is product code and is committed (D12). One-off recording helpers still stay local.

## Steps

| Step | Done when |
|---|---|
| Storyboard | `docs/demo/STORYBOARD.md`: a reviewer story (about 2 minutes: worklist run, lanes, a records-arrive round trip, sign) and an engineer story (about 3 minutes: the PHI boundary, a citation trace, `verify` catching an overclaim, the eval table). Every beat names the scenario and screen it needs. You approve it before anything else is built |
| Recorded mode | `llm_mode = "live" \| "recorded"`. A recording is one approved live run whose model responses are saved per (case key, node, criterion, attempt). They are keyed that way, not by prompt, because date offsets are random per run and prompts differ. Replay is deterministic and costs nothing. Recordings hold only de-identified model output |
| Scenarios | Named, resettable states: *overclaim caught* (a scripted model asserts an unsupported criterion and `verify` flags it), *records arrive* (a pending lab result released on request), *lab down*, *positive screen*. Each is reachable from a demo menu that only demo mode shows |
| Reset | One action returns the demo to the storyboard's starting state: cases, decisions, pending lab results, threads and sealed vaults (shredded and recreated) |
| Guided tour | An in-app overlay that walks each story, built with a small open-source library (license checked at the time). It can be turned off, respects reduced motion, and passes the WORKLIST_UI checklist |
| Click-through capture | You capture both stories in the chosen tool using the storyboard; the demo agent writes the step text, hotspots and branching. Published links and the analytics setup are recorded in `docs/demo/` |
| Hosted demo | With M12: password gate, recorded mode by default, the live switch behind its own spend cap, reset on a schedule, and the monthly cost written down |

## Picking the tool (D14)

All three gate **HTML capture** (a clickable copy of the live page) behind tiers of roughly $199 to $625 a month.
Their free and entry tiers capture screenshots or video. So the skill this milestone shows is storyboarding,
scenario design and reading the analytics, not HTML capture. Prices as found on 2026-10-24; check them again
before signing up, since the sources disagree:

| Tool | Free | Entry paid | Notes |
|---|---|---|---|
| Storylane | 1 published demo, basic analytics, lead capture, screenshot or video | Starter, $40–50 a month: unlimited demos, custom branding | HTML capture from Growth (about $500–625 a month) |
| Supademo | 5 guided demos, embed, no analytics | Scale, $38–50 a month: tracking links and analytics | Guided HTML demos from Business (about $199–249 a month) |
| Arcade | 3 published demos (Arcade's own guide; reviews say fewer), screen recording | Pro, about $32 a user per month | HTML capture on Growth or Enterprise; sources conflict |
