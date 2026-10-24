# M2: reference lab and screening labs

**Source:** new, designed in [LAB_STORE.md](../LAB_STORE.md). **Agents:** data, api, evals, web, qa.
**Checkpoint:** [CP2](../HUMAN_QA.md#cp2-the-reference-lab). **No live model spend** (scripted models; CP2 may use
one live case at your choice).

## Outcome

TB and hepatitis B results exist for most RA-active patients, some in the EHR and some only at a separate,
synthetic reference lab. The pipeline looks in both, cites which source each result came from, refuses
positive results as evidence of a negative screen, and never cites a pending result.

## Contracts (written before any task starts)

```python
# api/app/graph/state.py
class ResourceRef(BaseModel):
    resource_type: str
    id: str
    source: Literal["ehr", "lab"] = "ehr"

# api/app/graph/criteria.py
class EvidenceSpec(BaseModel):
    ...
    sources: list[Literal["ehr", "lab"]] = ["ehr"]   # Observation only may list "lab"
    value_in: list[str] | None = None                 # case-insensitive match on the result value
```

Lab MCP tools, as in [LAB_STORE.md](../LAB_STORE.md#interface). The lab result record is in
[LAB_STORE.md](../LAB_STORE.md#what-is-in-it). Settings: `LAB_URL` (blank starts the lab in process, as `MCP_URL` does).

## Tasks

| Task | Agent | Depends on | Done when |
|---|---|---|---|
| M2.1 Lab data generator | data | M1 | `data/generate_labs.py` writes `data/lab/results.json` from the cohort, with situations by hash of patient id in the agreed mix. Re-running gives identical output. A report prints counts per situation |
| M2.2 EHR screening observations | data | M2.1 | A post-step in `data/generate.sh` writes the EHR-situation Observations into the bundles. A bundle without them is unchanged byte for byte apart from the added entries |
| M2.3 Lab server | data | M2.1 | `api/app/lab/` store and FastMCP server; `lab` service in docker-compose on 8002; tests: patient scoping, pending hidden before `issued`, `release_pending` |
| M2.4 Schema and shared rules | api | contracts | `ResourceRef.source`, `EvidenceSpec.sources` and `value_in`; `support.py` checks them; YAML updated. Unit tests for a positive result, a wrong-source citation, and a value match ignoring case. **Shared file: no other task touches these meanwhile** |
| M2.5 Gateway and runtime | api | M2.3, M2.4 | A second `McpConnection` for the lab; the gateway routes lab tools, vaults accession ids and shifts lab dates with the patient's offset; `release_pending` is not callable through the gateway. PHI tests extended |
| M2.6 extract and verify | api | M2.5 | `extract` queries each listed source; `verify` resolves lab citations through `get_lab_result`. A lab outage fails the case with a named reason |
| M2.7 Ground truth and selection | evals | M2.1, M2.4 | `GroundTruth` reads the lab file, respecting `issued` against as-of; a screening situation per case; selection meets decision D4; new checks for wrong-source citations and pending results |
| M2.8 Source in the trace | api, then web | M2.6 | `cases.py` returns `source` per citation; `CitationTrace` shows "Reference Lab" or "EHR" |
| M2.9 QA and packet | qa | all | Full suite, `next build`, the CP2 walkthrough dry-run with scripted models, list of one patient per situation for CP2 |

Order: M2.1 → (M2.2, M2.3, M2.4 in parallel) → M2.5 → M2.6 → (M2.7, M2.8 in parallel) → M2.9.

## Risks

- **Ground truth and verify move together.** M2.4 changes the shared rule; CP2 reviews it explicitly.
- **Scripted models** in `api/tests` assume today's two gaps. Their fixtures change in M2.6, and the handoff lists
  every fixture changed.
- **The cohort changes** (M2.2 edits bundles), so the cohort fingerprint changes. M2.2 records the new one and
  M3 uses it.
