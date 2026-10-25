# M6: reviewer UX

**Source:** [WORKLIST_UI.md](../../WORKLIST_UI.md#build-steps) steps 6–8. **Agents:** web, qa (api only if
`limit`/`offset` is needed). **Checkpoint:** [CP6](../HUMAN_QA.md#cp6-reviewer-ux-outline).

Planned to the step; the orchestrator breaks it into tasks at CP5.

| Step | Done when |
|---|---|
| Case detail in the pane (step 6) | A nested route keeps `/cases/[id]` linkable; decision panel, Next in queue within a lane filter, Esc, focus on back, page restored on return. `PacketView` shows the reviewer's verdicts and notes |
| Pagination (step 7) | Rows fit the measured height; pager; reset rules; 120 cases page cleanly at desktop and phone |
| Phone (step 8) | Lane chips, two-line rows, 2×2 stat cards, search behind an icon, pinned decision bar; every action reachable at 375px without horizontal scroll |

QA runs the WORKLIST_UI checklist at each step, not only at the end. A 120-case list needs a cohort that size or
duplicated scripted cases; the qa agent says which it used.
