# Reference lab: a second source for screening results

Status: design, decisions D1–D4 settled at CP0 (2026-10-24), built in [M2](milestones/M02_reference_lab.md).

## Why

The policy asks for a negative TB screen (`tb_screening`, LOINC 71774-4, TB IGRA) and a hepatitis B screen
(`hepatitis_b_screening`, LOINC 5195-3, HBsAg) before a biologic. Synthea's RA module writes neither, so today
every RA-active packet has both as gaps. That case is kept on purpose in the criteria file, so `verify` has
something it must refuse to assert.

In practice these results often sit in a reference lab's system, not the EHR. A send-out lab (think Labcorp or
Quest; ours is fictional) runs the test and holds the result. The result may reach the EHR through an
interface, or it may not, and then someone has to fetch it. This design adds that second source, so the
pipeline has to look in two places and cite which one a result came from. It also makes "request records,
then they arrive" real for the worklist ([WORKLIST_UI.md](../WORKLIST_UI.md) step 5).

The lab is synthetic, like everything else. It has a made-up name ("Reference Lab" in code and on screen), and
no real lab's name, branding or formats.

## What is in it

A JSON file of lab results, generated from the cohort by a seeded script, served by its own small MCP server.

```
data/generate_labs.py         generator (committed)
data/lab/results.json         generated output (gitignored, like the Synthea bundles)
api/app/lab/                  store, tools and server for the reference lab (owned by the data agent)
```

One result looks like this, shaped like the EHR's `ObservationRecord` plus what a lab adds:

```json
{
  "id": "acc-7f3c9e21",
  "resource_type": "Observation",
  "source": "lab",
  "patient_id": "<the patient's MRN>",
  "code": {"system": "http://loinc.org", "code": "71774-4", "display": "TB IGRA"},
  "status": "final",
  "effective_date": "2025-11-04",
  "issued": "2025-11-06",
  "value": "Negative",
  "interpretation": "N",
  "performer": "Reference Lab",
  "comment": null
}
```

- **Patient match.** The lab knows a patient by MRN, not by the EHR's resource id. Synthea writes an MRN
  identifier (`v2-0203` code `MR`) whose value equals the patient id, so the match is exact in this cohort.
  Demographic matching is out of scope; the design says so rather than pretending to solve it.
- **`issued`** is when the lab released the result. A result with `issued` after the run's as-of date is
  *pending*: the lab has it, but the tools do not return it yet. This is what makes records "arrive".
- **`comment`** is free text from the lab. It is untrusted input, like a note, and M8 plants injection cases
  in it.

## Who has what

Each patient's screening situation is a pure function of their patient id (a hash, not a random draw), so
regenerating or growing the cohort never changes anyone's situation. The mix (D2):

| Situation | Share | TB and HBsAg results |
|---|---|---|
| In the EHR | 20% | Both negative, written into the Synthea bundle as Observations (the lab is interfaced) |
| At the lab only | 35% | Both negative, at the lab, not in the EHR |
| Split | 10% | One in the EHR, the other at the lab |
| Pending at the lab | 10% | Both negative, `issued` after the as-of date: arrive when requested (M5) |
| Positive | 10% | A positive IGRA or reactive HBsAg, at the lab |
| Nowhere | 15% | Neither: a true gap, as today |

"In the EHR" means changing the bundles. That runs as a post-processing step in `data/generate.sh`, as
WORKLIST_UI.md already says making screening real requires. The step writes FHIR Observations with Synthea-style
ids and leaves everything else in the bundle unchanged.

## Interface

A second MCP server, `lab` in docker-compose (port 8002), with three read-only, patient-scoped tools:

| Tool | Returns |
|---|---|
| `search_lab_results(patient_id, code, limit=5)` | Released results for that LOINC code, oldest first. `code` required |
| `get_lab_result(patient_id, result_id)` | One result, for citation checks. Another patient's id does not resolve |
| `release_pending(patient_id)` | Demo control for M5: marks that patient's pending results as issued now. Not exposed to the agent |

**Decided (D1):** a separate service. The alternative is a second store inside the existing FHIR
server, which is less work but hides the point: two systems, two connections, and results that have to say
where they came from. With a separate service, the API's `Runtime` holds two `McpConnection`s, and the lab
can be down while the EHR is up. That is a failure case worth having.

## What changes in the pipeline

| Where | Change |
|---|---|
| `api/app/graph/state.py` | `ResourceRef` gains `source: Literal["ehr", "lab"] = "ehr"`. Default keeps every existing packet valid |
| `api/app/graph/criteria.py` | `EvidenceSpec` gains `sources` (default `["ehr"]`; Observation screening specs list both) and `value_in` (e.g. `["Negative", "Non-reactive"]`) |
| `data/payer_criteria/adalimumab_ra.yaml` | The two screening criteria list both sources and require a negative value. The comment saying the chart cannot support them is updated |
| `api/app/phi/gateway.py` | Routes lab tools to the lab session. Lab results are de-identified with the same per-patient vault and date offset, and the accession id gets a placeholder kind |
| `api/app/graph/nodes/extract.py` | Queries each source a spec names; each `EvidenceItem` records its source |
| `api/app/graph/support.py` | `spec_supports` checks source and `value_in`. Shared with ground truth, so this changes both on purpose |
| `api/app/graph/nodes/verify.py` | Resolves a lab citation with `get_lab_result`; an EHR id cited as a lab result (or the reverse) does not resolve |
| `evals/dataset.py` | Ground truth reads the lab file too, respecting `issued` against as-of |
| `evals/experiment.py` | A screening axis for case selection (see below) |
| `api/app/routes/cases.py`, `web/components/CitationTrace.tsx` | The trace shows "Reference Lab" or "EHR" for each citation |

### Positive results

Today `verify` checks a citation's code and status, never its value. A positive IGRA would count as evidence of
"negative TB screening". `value_in` fixes that. A positive result then leaves the criterion not established,
and the packet should say why: the screen was positive, not missing. Where it lands in the worklist (D3):

- **Decided:** it lands in *Resolve flagged claims* with the reason "screening positive". The reviewer
  decides, because a positive latent-TB screen usually means treatment first, not a denial.
- Not chosen: a sixth lane, *Clinical follow-up*.

### Lab down

If the lab server is unreachable, `extract` records the screening criteria as not checked rather than not
found, and the case fails with that reason instead of producing a packet with false gaps. The existing
`McpConnection` reconnect handles brief outages.

## Evals

- Ground truth changes for every RA-active case, so the M3 baseline is a new baseline, not a comparison with
  Run 3. RESULTS.md says so.
- New situations to sample: screening in EHR, at lab, split, pending, positive, nowhere. Crossing them with the
  six existing situations makes 36 strata, too many to fill with 5 cases each. **Decided (D4):** select on the
  existing axis as today, and check separately that every screening situation has at least 5 cases across
  the selection.
- New checks: a lab citation resolves only against the lab; a pending result is never cited before release;
  a positive result is never asserted as meeting the criterion.

## PHI and the BAA-free property

Nothing changes in kind: the lab is another synthetic source on the far side of the PHI gateway. Its records
carry the same identifiers the EHR's do (patient id, dates) and get the same treatment. Accession ids are
real-looking identifiers and are vaulted like resource ids. The lab's `comment` passes through the prompt guard
like note text.

## Out of scope

Demographic patient matching, HL7 v2 or real lab interfaces, ordering a test (the lab only holds results),
results for other tests.
