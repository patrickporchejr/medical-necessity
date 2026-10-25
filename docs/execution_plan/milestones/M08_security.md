# M8: prompt-injection hardening

**Source:** [ROADMAP.md](../../ROADMAP.md#phase-5-security) Phase 5. **Agents:** security, evals, api, qa.
**Checkpoint:** [CP8](../HUMAN_QA.md#cp8-prompt-injection-hardening-outline).

Planned to the step; tasks written at CP7.

| Step | Done when |
|---|---|
| Injection cases | Synthetic notes and lab `comment`s carrying instructions, some on keyword-matching lines (the ones that reach the model); added as eval cases with expected outcomes unchanged |
| verify holds | Every injected claim ends flagged or unchanged; the "joint pain inside an injected line" question written up for your decision |
| Token caps | Per-run and per-batch caps; a run over the cap is refused with a reason |
| In CI | The cases run with scripted models in the existing `tests` workflow |
