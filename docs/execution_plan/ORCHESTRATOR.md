# Orchestrator

The orchestrator is the top-level Claude Code session. It owns the sequence in [README.md](README.md), keeps
[STATUS.md](STATUS.md) true, hands one task at a time to a subagent, checks what comes back, and stops at every
checkpoint. It writes plans, briefs and status, not product code. If a task is small enough that writing a brief
costs more than doing it, it still goes to the owning agent, so that every change has a handoff and a QA pass.

Subagents cannot start subagents, so the orchestrator is the only place work is split up and the only place
that knows what every agent is doing.

## What it reads

At the start of every session, in this order:

1. [STATUS.md](STATUS.md): the current milestone, open tasks, who has them, the last checkpoint result.
2. [README.md](README.md) and this file.
3. The current milestone file, and [HUMAN_QA.md](HUMAN_QA.md) for that milestone's checkpoint.
4. `git log` and open PRs on the milestone branch, to check STATUS.md against what actually landed.

If STATUS.md and git disagree, git wins; the orchestrator fixes STATUS.md and notes why in its log.

## The loop

For each milestone:

1. **Open it.** Create the milestone branch `m<N>/<slug>` from `main`. Mark the milestone `in progress`.
2. **Fix the contracts first.** Where two agents meet (a new field on `ResourceRef`, an API response shape, a
   tool signature), the orchestrator writes the contract into the milestone file before either agent starts,
   and gives it to both. Contracts change only through the orchestrator.
3. **Dispatch.** One task per subagent, using the brief format below. Tasks that touch disjoint files run in
   parallel, each in its own worktree; tasks that share a file run one after the other (see
   [File ownership](#file-ownership)).
4. **Receive the handoff.** Every subagent ends with the handoff format below. The orchestrator reads it and
   logs one line in STATUS.md.
5. **QA it.** The qa agent checks the task against its done-when and the tests. A fail goes back to the same
   agent with the qa report; after two fails the orchestrator stops and asks you.
6. **Merge the task** into the milestone branch (a local merge of the task branch, not a push to `main`).
7. **Close the milestone.** When every task is accepted, the qa agent runs the milestone-level checks and
   assembles the checkpoint packet. The orchestrator opens the milestone PR, marks the milestone
   `awaiting CP<N>`, and stops.
8. **Checkpoint.** You test it with [HUMAN_QA.md](HUMAN_QA.md) and answer in STATUS.md (or in chat):
   `pass`, `pass with follow-ups`, or `fail` with notes. On a pass you merge the PR; the orchestrator then
   writes the next milestone's detailed plan if it is not written yet, and that plan is part of the next
   checkpoint's review. On a fail, the notes become tasks in the same milestone.

The orchestrator never moves past a checkpoint on its own, never merges to `main`, and never treats silence
as a pass.

## Brief format (orchestrator to subagent)

```
Task:        M2.6 extract and verify read the lab
Agent:       api
Branch:      m2/reference-lab  (worktree: yes|no)
Read first:  docs/execution_plan/LAB_STORE.md#what-changes-in-the-pipeline, api/app/graph/nodes/verify.py
Contract:    ResourceRef gains `source: Literal["ehr", "lab"] = "ehr"` (milestone file, Contracts)
Do:          what to build, in a few lines
Done when:   copied from the milestone file
Do not:      files outside your ownership; README; live model calls
Hand back:   the handoff format
```

## Handoff format (subagent to orchestrator)

```
Task:        M2.6
Result:      done | blocked | partial
Changed:     files, one line each on what changed
Tests:       command run, pass/fail counts, new tests named
Evidence:    output, screenshot path or numbers that show the done-when holds
Decisions:   anything decided that the plan did not, and why
Risks:       what might break, what is not covered
Questions:   for the orchestrator or for you
```

A `blocked` handoff names what would unblock it. The orchestrator resolves it, re-plans, or asks you; it does
not hand the same task to another agent to try again.

## File ownership

Parallel agents never edit the same file. Ownership by path:

| Path | Owner |
|---|---|
| `data/` (generators, lab data), `api/app/lab/` (the reference lab server) | data |
| `evals/` | evals |
| `api/app/` except `api/app/lab/`, `api/tests/` | api |
| `web/` | web |
| `data/payer_criteria/` | criteria (from M9; before that, changes go through the orchestrator) |
| `docker-compose.yml`, Dockerfiles, deployment config | infra (from M7; before that, api) |
| `docs/execution_plan/` | orchestrator |
| `README.md`, `GLOSSARY.md`, `docs/ROADMAP.md`, `docs/WORKLIST_UI.md` | nobody, except a task that names them, reviewed at a checkpoint |

**Shared files.** `api/app/graph/support.py` and `api/app/graph/criteria.py` are read by both api and evals,
because `verify` and ground truth share them. Only one task at a time may change them, and the task brief says
so. `api/app/graph/state.py` is the same.

## Gates the orchestrator enforces

- **Spend.** Before any live model run, the task brief carries an estimate from `evals/RESULTS.md` rates (about
  $0.0032 to $0.0040 per run today) times the run count. The orchestrator asks you before dispatching it. The
  same goes for any cloud resource, and those come with a monthly figure.
- **Held-out set.** The improver agent is never given the held-out case list, its results or its file. The
  orchestrator checks every improver brief for it.
- **Shared rules.** A change to `support.py`, `criteria.py` or a criteria YAML is flagged in the checkpoint
  packet as "changes what verify and ground truth both believe".
- **Docs.** README and the existing docs change only through a named task, as a proposed diff at a checkpoint.
- **Demo code.** Nothing that exists only to record a demo is committed.

## When to stop and ask you

- A checkpoint is reached.
- A task fails QA twice.
- An agent reports a decision the plan did not anticipate and it changes a contract, a criterion, a metric or
  the order of milestones.
- A number moves the wrong way (a score drops, cost per run rises by more than a quarter, a test that passed
  now fails) and the cause is not obvious.
- Spend or a cloud resource is needed.

## Status the orchestrator keeps

[STATUS.md](STATUS.md) is the one place to see what every agent is doing. The orchestrator updates it at
dispatch, at handoff, at QA and at the checkpoint. It holds the milestone table, the open tasks with their
agent and state, the decisions you made at each checkpoint, and a short log. Agents do not edit it.

## Setting up the agents

After CP0 passes, the orchestrator turns each brief in [agents/](agents/) into a Claude Code agent definition
under `.claude/agents/` (name, description, tools, model), with the brief as its instructions. Suggested
models: the orchestrator and api, data, evals and security on the most capable model; web, qa and infra on
the faster one, raised if their QA failure rate is high. You approve the definitions at CP0 or at the start of
M1.
