# web agent

**Role:** the reviewer dashboard, built to [WORKLIST_UI.md](../../WORKLIST_UI.md).
**Active in:** M2 (source in the citation trace), M4, M5, M6, M11.

## Owns

`web/`.

## Reads first

[WORKLIST_UI.md](../../WORKLIST_UI.md) (the design wins over the prototype), `web/lib/api.ts`, the components
the task names, and the API contract in the milestone file.

## Rules

- Build against the contract. If the API does not return what the screen needs, say so; do not compute verdicts
  in the browser.
- The [checklist for every step](../../WORKLIST_UI.md#checklist-for-every-step) applies to every task: contrast,
  touch targets, reduced motion, live region, no reordering during runs, no consequential action without context.
- Reviewer words, not node names (the WORKLIST_UI.md table).
- Each step leaves the dashboard working; existing routes keep working until replaced.
- Screenshots at desktop width and at 375px go in the handoff for any visible change.

## Done means

`cd web && npx next build` passes, the screenshots match the done-when, and the checklist is ticked in the handoff.
