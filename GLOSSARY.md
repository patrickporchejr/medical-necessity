# Glossary

Terms and acronyms used in this repo, grouped by where they come from. Where a term names something in the code, the file is given.

## Clinical and payer terms

| Term | Meaning |
|---|---|
| **Prior authorization (PA, prior auth)** | A payer's approval, required before a service or drug is covered. The workflow this project automates the evidence-gathering for. |
| **Medical necessity** | The payer's test that a service is appropriate for the patient's condition, judged against written criteria. |
| **Payer** | The insurer (or plan) that pays for care and sets the coverage criteria. |
| **Payer criteria** | The conditions a request must meet to be approved. Here, one synthetic policy in `data/payer_criteria/adalimumab_ra.yaml`. |
| **Service line** | The service a policy covers. This repo has exactly one: adalimumab for rheumatoid arthritis. |
| **Step therapy** | A policy shape that requires trying a cheaper therapy first (here, methotrexate) before the requested one is covered. |
| **RA** | Rheumatoid arthritis, the condition every patient in the cohort has. |
| **Adalimumab** | A biologic TNF inhibitor used to treat RA; the drug the synthetic policy covers. |
| **TNF inhibitor** | A biologic that blocks tumor necrosis factor, an inflammatory signal. |
| **Biologic** | A drug made from living cells. The policy requires TB and hepatitis B screening before starting one. |
| **DMARD** | Disease-modifying antirheumatic drug. Methotrexate is the conventional DMARD the policy requires a 90-day trial of (`dmard_trial`). |
| **TB IGRA** | Interferon-gamma release assay, a tuberculosis screening test (LOINC 71774-4). |
| **HBsAg** | Hepatitis B surface antigen, a hepatitis B screening test (LOINC 5195-3). |
| **Denial rate, A/R days** | Revenue-cycle measures: the share of claims a payer denies, and the days money stays in accounts receivable before it is paid. |
| **CMS** | Centers for Medicare & Medicaid Services, the US agency whose electronic PA rules tighten through 2027. |
| **X12 278** | The standard EDI transaction for submitting a prior authorization request. Out of scope here. |
| **Clearinghouse** | An intermediary that routes and validates claims and transactions between providers and payers. Out of scope here. |
| **EHR** | Electronic health record. This repo reads Synthea bundles instead of a real one. |
| **Write-back** | Writing a result into the EHR as an order or record. Out of scope: the packet leaves as a document. |

## Data and standards

| Term | Meaning |
|---|---|
| **FHIR (R4)** | Fast Healthcare Interoperability Resources, the HL7 standard for exchanging health records as typed resources. R4 is the release the bundles use. |
| **Resource** | One FHIR record, with a `resource_type` and an `id`. The types used here: **Condition** (a diagnosis), **MedicationRequest** (an order for a drug), **Observation** (a lab or measurement), **DocumentReference** (a clinical note). |
| **Bundle** | A FHIR document holding many resources; Synthea writes one per patient. |
| **Synthea** | An open-source generator of synthetic patient records. All patient data here comes from it (`data/generate.sh`). |
| **Cohort** | The fixed-seed set of generated patients with RA. |
| **SNOMED CT** | A clinical terminology for diagnoses and findings; RA is code 69896004. |
| **RxNorm** | A terminology for drugs; methotrexate 2.5 MG oral tablet is 105585. |
| **LOINC** | A terminology for lab tests and observations. |
| **SMART on FHIR** | The standard for apps and backend services to authorize against a real EHR's FHIR API. What a real deployment would put behind the MCP interface. |

## Privacy and compliance

| Term | Meaning |
|---|---|
| **PHI** | Protected health information under HIPAA. This repo holds none: the data is synthetic. |
| **HIPAA** | The US law governing the privacy and security of health information. |
| **BAA** | Business associate agreement, the HIPAA contract required before a vendor handles PHI. Not needed here. |
| **De-identification** | Removing or replacing identifying values. Here, done in the PHI gateway before the graph sees any chart data. |
| **PHI gateway** | `api/app/phi/gateway.py`, the only path from the graph to the chart. It de-identifies every tool result. |
| **Vault** | `api/app/phi/vault.py`, the in-memory, per-run map between placeholders and real values (the **re-ID map**). Never written to disk; it expires after a TTL and is cleared when the run ends. |
| **Placeholder** | The stand-in the gateway issues for a real value, e.g. `<CONDITION_1>`. The angle brackets are part of the id. |
| **Date shifting** | `api/app/phi/dates.py`. Dates are moved by a per-patient offset rather than redacted, so durations still compute. |
| **Deny-list** | Names learned from the patient's structured records, matched in free text instead of using NER. |
| **Rehydration** | `api/app/phi/rehydrate.py`. Swapping placeholders back to real values in the packet and verdicts returned to the reviewer. |
| **Prompt guard** | `api/app/llm/guard.py`. Refuses any outbound prompt holding a value the vault knows to be real. |
| **Presidio** | Microsoft's de-identification library; used here for its pattern recognizers (phone, email, SSN). |
| **NER** | Named-entity recognition. Turned off on purpose: spaCy missed patient names and flagged clinical text. |
| **spaCy** | The NLP library Presidio uses for NER by default. |
| **SSN** | Social Security number. |
| **Zero data retention (ZDR)** | A provider-account setting under which prompts and outputs are not stored. Nothing in this code configures it. |
| **TTL** | Time to live: how long the vault keeps its map before it expires. |

## The pipeline

| Term | Meaning |
|---|---|
| **Case** | One prior auth request: one patient against the payer criteria. |
| **Chart** | A patient's records, read through the MCP tools. |
| **Criterion** | One requirement in the payer criteria, with an id: `ra_diagnosis`, `dmard_trial`, `active_disease`, `tb_screening`, `hepatitis_b_screening`. |
| **Graph** | The LangGraph `StateGraph` in `api/app/graph/build.py`: `extract → assemble \| gap_packet → verify`. |
| **Node** | One step in the graph, a plain async function in `api/app/graph/nodes/`. |
| **State** | The typed data passed between nodes (`api/app/graph/state.py`). |
| **Conditional edge, route** | The branch after `extract`, made by `route_after_extract`: `assemble` if the RA diagnosis is established, `gap_packet` if not. |
| **`extract`** | Pulls the chart evidence bearing on each criterion via MCP tools. |
| **`assemble`** | Has the model draft the packet from that evidence. |
| **`gap_packet`** | Writes the packet in code, without a model, when the diagnosis is not established. |
| **`verify`** | Resolves each assertion's citations back to chart records and flags any that are unsupported. |
| **`run_pipeline`** | The one runner for a case: makes the vault, gateway and guard, runs the graph, rehydrates the result. |
| **Established** | A criterion the chart actually meets. For `ra_diagnosis`, an active (not resolved) RA Condition. The same rule drives the route, `verify` and the eval ground truth. |
| **Packet** | The medical-necessity document drafted for the reviewer: one assertion per criterion. |
| **Assertion** | A statement about one criterion, of kind **evidence** or **gap**. |
| **Evidence assertion** | Says the chart meets the criterion, and must cite the records that show it. |
| **Gap** | Says the chart does not establish the criterion, and cites nothing. Lets the packet state a missing criterion without fabricating evidence. |
| **False gap** | A gap written for a criterion the chart does establish. |
| **Citation** | A `ResourceRef`: the `resource_type` and `id` of the chart record an assertion rests on. |
| **Unsupported** | An assertion `verify` could not back with the chart. Flagged for the reviewer, never dropped. |
| **Citation repair** | `repair_citations` in `assemble.py`: restores the angle brackets on a bare placeholder id (`CONDITION_1` → `<CONDITION_1>`) and counts each repair. |
| **As-of date** | The date durations are computed against, pinned for evals (`AS_OF_DATE`). |
| **Reviewer** | The human who approves, edits or rejects the packet. The dashboard and routes for this are planned. |
| **HITL** | Human in the loop: a person approves the model's output before it is acted on. |

## Evals and metrics

Defined in `evals/metrics/citation_resolution.py` and `evals/experiment.py`; results in `evals/RESULTS.md`.

| Term | Meaning |
|---|---|
| **Ground truth** | The expected answer for each criterion, read from the raw Synthea bundles rather than from a model. |
| **Experiment** | A LangSmith run of the whole dataset on one model (`evals/experiment.py`). |
| **Dataset** | The eval cases (`prior-auth-adalimumab-ra` in LangSmith), chosen by `select_patients`. |
| **Situation** | A combination of chart facts the rules distinguish (e.g. "RA active, no order, active notes"); the dataset takes cases from each. |
| **Citation resolution rate** | The headline metric: of the evidence assertions in a packet, the fraction whose citations point at a record that exists and supports the criterion. |
| **Citation level rate** | The finer grain: resolving citations over all citations made. |
| **Gap accuracy** | Of the criteria the packet calls gaps, the fraction that truly are. |
| **Decisions correct** | Evidence vs gap right on every criterion. |
| **Packet fully correct** | Right calls, every claim sound, nothing unaddressed. |
| **`ids_well_formed`** | True when a run's citations needed no repair. |
| **Faithfulness** | Whether an assertion's wording matches its source. Not measured: there is no LLM judge. |
| **LLM judge** | Using a model to score another model's output. Deliberately not used. |
| **Scripted model** | A fake model with canned replies, used by the harness tests so they make no model calls. |

## Tooling and infrastructure

| Term | Meaning |
|---|---|
| **LLM** | Large language model. Providers here: Anthropic (Claude) and Google (Gemini), chosen with `LLM_PROVIDER`. |
| **LangChain** | The framework whose chat models wrap both providers (`api/app/llm/langchain_client.py`). |
| **LangGraph** | The library the pipeline's state graph is built with. |
| **LangSmith** | LangChain's tracing and evaluation service. Here it receives metadata-only traces and hosts the experiments. |
| **Trace, span** | A recorded run and its nested steps (one per graph node). |
| **Allowlist** | The set of keys the LangSmith client is permitted to send; everything else is dropped (`api/app/observability.py`). |
| **MCP** | Model Context Protocol, the standard for exposing tools to a model. The chart is served as read-only FHIR tools over it. |
| **FastMCP** | The Python library the MCP server is built with (`api/app/mcp/server.py`, port 8001). |
| **Streamable HTTP** | The MCP transport the server uses. |
| **FastAPI** | The Python web framework for the API (port 8000). |
| **SSE** | Server-sent events: how node events will stream to the dashboard (planned). |
| **Next.js** | The React framework for the reviewer dashboard (`web/`, port 3000; placeholder pages). |
| **Eval gate** | A CI check that runs the evals on each PR (`.github/workflows/evals.yml`; a placeholder step). |
