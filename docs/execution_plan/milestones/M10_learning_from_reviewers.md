# M10: learning from reviewers

**Source:** [ROADMAP.md](../../ROADMAP.md#phase-3-learning-from-reviewers) Phase 3. **Agents:** improver, evals, qa.
**Checkpoint:** [CP10](../HUMAN_QA.md#cp10-learning-from-reviewers-outline).

Planned to the outcome; tasks written at CP9b.

- Input: the audit record from M5 and M7 (decisions, notes, diffs against drafts). The decisions from your
  checkpoint testing are most of it; the packet says how many and that they are one reviewer's.
- The improver clusters edits and rejections and proposes one prompt or criteria change as a PR, with before and
  after scores on the working set. The evals agent, not the improver, scores the held-out set.
- The faithfulness metric the roadmap names reverses a recorded decision (no LLM judge, #21). It is proposed with
  its cost per run and needs your yes before it is built.
- Done when one change moves a held-out metric by more than run-to-run variance, or the write-up shows that
  none did.
