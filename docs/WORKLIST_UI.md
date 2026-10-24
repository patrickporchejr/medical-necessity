# Worklist UI: design and build steps

Status: design, 2026-10-24. Nothing here is built. This is the target for the dashboard once the worklist from
[ROADMAP.md](ROADMAP.md) Phase 1 lands, and the order to build it in. Each step ships on its own and leaves the
dashboard working.

Prototype: a clickable mockup with simulated runs, at <https://claude.ai/artifact/R3d9PoRGi5WTNgWrZLVWWn>
(private to the owner; share it from its page to give others access). Where this doc and the prototype
disagree, this doc wins: the prototype fakes data the pipeline does not produce (see [What the prototype
fakes](#what-the-prototype-fakes)).

## The idea

The reviewer presses one button and every case in the worklist runs. They watch it on one screen, without
opening a case to see how it is going, and a review queue on the right says what needs a person and why.

The pipeline drafts and checks; the human makes five kinds of decision. Making those explicit is the point of
the design, because the current dashboard shows a packet and stops there.

| Lane | When a case lands here | What the reviewer decides |
|---|---|---|
| Resolve flagged claims | `verify` left an assertion unsupported, or a criterion unaddressed, after the repair loop | Mark it as a gap, or override with a written reason |
| Ready to sign | Every criterion has supported evidence | Sign, after attesting they reviewed the packet. The system never submits |
| Get missing records | At least one criterion is a supported gap (the chart cannot show it) | Request the records from the clinic; when they arrive, re-run the case |
| Confirm not eligible | Route was `gap_packet`: no active RA diagnosis | Close as not eligible, or return to the prescriber |
| Runs that failed | Case status `failed` | Re-run it |

A case that has been signed, closed or returned is **resolved**. Lane priority, top to bottom, is the order
above. "Next in queue" walks cases in that order, skipping ones waiting on the clinic. When a lane filter is
on, it stays inside that lane ("Next in Ready to sign (6) →") and says "lane cleared" when the lane is empty.

## Target design

### Desktop (over 900px)

- **Header, fixed:** title and service line, patient search, Start worklist (Pause, Resume, Run complete),
  Reset (two clicks, since it clears decisions).
- **Two panes that scroll on their own, no visible scrollbars.** Left: the work. Right, 330px: the review
  queue. Both end at the same line; the page itself never scrolls.
- **Stat cards (4):** cases drafted (with a progress bar), running now (and how many at a time), waiting on
  you (navy, the one to watch), estimated model cost.
- **Cases table**, one row per case, fixed order while runs progress:
  Patient · Progress (current step in plain words, plus a thin bar) · one column per criterion · Result ·
  Needs you (a pill naming the lane). Tabs above it: All, In progress, Needs you, Resolved. Clicking a row
  opens the case.
- **Pagination, not scrolling.** The table fills the space under the stat cards and fits as many rows as the
  height allows (recomputed on resize). Pager below: "1–9 of 48", previous, page numbers with gaps, next.
  Changing a tab, lane or search returns to page 1; opening a case and coming back keeps the page.
- **Review queue:** one row per lane: count, title, one line on the decision. Clicking a lane filters the
  table to it (again clears it); the active filter shows as a removable chip by the table title. A lane with
  a safe bulk action shows it above the table: "Request records for all N", "Re-run all N". Resolved sits
  last, below a divider.
- **Case detail** opens in the left pane; the queue stays visible. "← All cases" (and Esc) returns to the
  list at the same page. "Next in queue (N) →" moves to the next case needing a decision. Then, top to
  bottom:
  1. Name, status, patient key, as-of date, model and cost.
  2. **Your decision** panel, only when the case is in a lane: what is needed and the actions. All
     consequential actions live here, beside the packet, never in the list or the queue.
  3. Timeline of steps with seconds.
  4. The packet, criterion by criterion: plain name and id, evidence or gap, the verdict (verified,
     flagged, or the reviewer's decision, labelled as theirs), `verify`'s reasons, and each citation with
     the record it resolves to (notes expand in place).

### Phone (under 700px)

- Header on one line: title and Start; search behind an icon.
- The queue moves to the top as a scrolling strip of lane chips (count and title), still filtering the list.
- Stat cards in a 2×2 grid, values only.
- Cases as two-line rows: name and Needs-you pill, then the five criterion marks (labelled) and the step.
  Ten per page; the page scrolls.
- In the case detail the decision actions are pinned to the bottom of the screen.

### Words on screen

Reviewers should not need to know node names. The detail timeline may show them small, for engineers.

| Graph node | Status while running | Timeline label |
|---|---|---|
| `extract` | Reading chart | Read chart |
| `assemble` | Drafting | Draft |
| `gap_packet` | Writing gaps, no model | Write gaps |
| `verify` | Checking citations | Check citations |
| `assemble` (repair) | Repairing | Repair draft |

Case states: Not started (before Start), Queued, the running step, Drafted, Run failed, and for resolved cases
the outcome (Signed, Closed · not eligible, Returned to prescriber).

Criterion marks: ✓ evidence verified (green), – gap (amber), ! flagged (red), a letter in an outline while the
draft is unchecked, — not drafted, and navy for a reviewer's decision or a received record. Marks carry a text
label (`title` and `aria-label`), not color alone.

### Look

Teal and navy on a pale mint ground, white panels, Plus Jakarta Sans. Values from the prototype:

| Token | Value | Use |
|---|---|---|
| ground | `#edf8f7` | page background |
| teal | `#1b7684` | primary buttons, stat cards, active states |
| navy | `#24527a` | table header, "waiting on you", reviewer decisions |
| ink / muted | `#102a33` / `#5a6f75` | text (muted passes 4.5:1 on white and on the ground) |
| line | `#dcebea` | borders |
| green / amber / red | `#1e7d4f` / `#9a5b00` / `#b42318` | evidence, gaps and missing records, flags and failures |

Dark mode is not designed yet; the current dashboard has one, so step 1 should keep a dark set of the same
tokens.

## Build steps

What exists today: `web/app/page.tsx` (patient picker, one case at a time, a table polled every 1.5s while
anything runs), `web/app/cases/[id]/page.tsx` (timeline, summary, `PacketView`, `CitationTrace`), and
`api/app/routes/cases.py` (in-memory cases, `MAX_RUNNING = 2`, `GET /cases` returns cases without results or
events). The steps keep the existing components and routes working until they are replaced.

### Step 1: tokens and shell

- Replace the colors in `web/app/globals.css` with the tokens above (light and dark), load Plus Jakarta Sans.
- New layout: fixed header, two panes with independent, scrollbar-free scrolling, the phone breakpoint.
  The queue pane can hold a placeholder.
- Done when: the existing pages render in the new shell at desktop and phone widths, nothing else changed.

### Step 2: the list carries what the table needs

The table needs each case's progress and per-criterion verdicts without fetching every case.

- API: `GET /cases` returns a `summary` per case: current node, nodes finished out of expected, per criterion
  `{kind, supported}` once verified (the draft's `kind` before), `route`, `repairs`. Metadata and verdicts
  only, no assertion text, so the list stays small.
- Web: the cases table, tabs and search, from that summary. Keep polling: 1.5s while anything runs, 5s idle.
- Done when: a run's progress and marks update in the table without opening the case.

### Step 3: run the worklist

- API: `POST /cases/batch` creates a case for every patient (or a given list) and queues them. Make
  `MAX_RUNNING` a setting. Pause stops new cases starting (running ones finish); Resume releases them.
  `POST /cases/{id}/rerun` for a failed case.
- Watch the limits ROADMAP.md Phase 1 names: one shared MCP session, and the MCP server's 8-chart LRU.
  Measure before raising `MAX_RUNNING` past 8.
- Web: Start worklist / Pause / Resume / Run complete; the stat cards.
- Done when: one click runs the whole cohort and the stat cards and table follow it.

### Step 4: lanes

- API: each case gets `lane` (one of the five, `resolved`, or none while running), derived from status,
  route, `verify`'s verdicts and the decisions below. Derive it in one function with a test per lane.
  A gap only counts as missing when `verify` supported it (the chart really does not establish it); an
  unsupported gap is a flag.
- Web: the review queue (counts, lane filter, chip), the Needs-you column.
- Done when: every finished case sits in exactly one lane, and the counts match the table.

### Step 5: decisions and the audit record

This is the first stored state that is the reviewer's, not the pipeline's.

- API: `POST /cases/{id}/decisions` with `{action, note?}`; actions `mark_gap`, `override` (note required),
  `request_records`, `close`, `return`, `sign` (attestation required). Each decision is appended with a
  timestamp to the case and to an audit log, and changes the lane. Bulk: request records and re-run for many
  cases in one call.
- Records that "arrive" have to enter the chart and the case has to be re-run so `verify` sees them. With
  Synthea there is no way to add a record yet; until there is, "records received" is a manual decision and
  should say so on screen.
- In memory first; Postgres when ROADMAP.md Phase 1 adds durable state. Storing decisions means storing the
  rehydrated packet's context with real ids; ROADMAP.md notes this.
- Done when: every lane's actions work, decisions survive a page reload, and the audit log shows who did
  what (one reviewer, no auth yet).

### Step 6: case detail in the pane

- Move the case page into the left pane (a nested route keeps `/cases/[id]` linkable), with the decision
  panel, Next in queue, Esc and focus on the back button, and the list's page restored on return.
- Reuse `PacketView` and `CitationTrace`; add the reviewer's verdicts and notes to `PacketView`.
- Done when: a reviewer can clear a lane from the detail view without going back to the list.

### Step 7: pagination

- Rows that fit the measured height, the pager, page reset rules. Client-side paging over the polled list
  is enough for the cohort; add `limit` and `offset` to `GET /cases` past a few hundred cases.
- Done when: 120 cases page cleanly at desktop and phone sizes.

### Step 8: phone

- Lane chips, two-line rows, 2×2 stat cards, search behind an icon, pinned decision bar.
- Done when: every action is reachable at 375px without horizontal scrolling.

### Step 9: live updates (later)

Polling is fine for a demo. SSE over `PipelineEvent` (metadata only) is the README's planned stream and
replaces polling when it lands.

## Checklist for every step

From the prototype's QA, so these don't come back:

- No selection state that lingers after navigation; focus rings only for keyboard focus.
- Text at least 4.5:1, including muted labels and placeholder marks; no lowered opacity on whole rows.
- Touch targets at least 44px (40px for dense pager and table controls).
- Pulsing or animated states respect `prefers-reduced-motion`.
- Status changes announced through a polite live region.
- Rows never reorder while a run is in progress.
- No consequential action without context: sign only from the detail view with attestation, override only
  with a note, destructive actions confirmed.
- Button labels say what happens ("Return to prescriber", not a second "Close").

## What the prototype fakes

- **Cases and timings.** Names other than Loyd638 and Denis399 are generated; timings and costs are rough
  figures from `evals/RESULTS.md`; record ids, notes and dates are invented.
- **Screening labs.** About half the generated clean cases carry negative TB and hepatitis B results so that
  Ready to sign fills on the first pass. The cohort has none: Synthea's RA module writes no such labs, so
  today every RA-active packet has those two gaps. Making this real means changing `data/generate.sh` (or
  the Synthea configuration) to write those observations, regenerating the cohort, and re-running the evals,
  whose expected outcomes will change.
- **"4 at a time".** The API runs 2 (`MAX_RUNNING`); the prototype's default is a tweak.
- **Records arriving, signing, overriding.** None of these exist in the API yet (steps 4 and 5).

## Open questions

- Who is the reviewer? There is no auth, so the audit log records one anonymous reviewer until there is.
- What does "request records" do outside the demo: a status only, a generated letter, or a message?
- Should "Next in queue" include cases waiting on the clinic once their records are overdue?
