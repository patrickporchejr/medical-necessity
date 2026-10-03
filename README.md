# Medical Necessity AI Engine

A clinical evaluation engine built with **LangGraph** and **LangSmith** to automate prior authorization and medical necessity checks.

The agent reads a synthetic patient chart through MCP FHIR tools, gathers the chart evidence for each of the payer's criteria, drafts the medical-necessity packet, and verifies that every assertion in that packet resolves to a real record in the chart. A human reviewer approving, editing, or rejecting the packet is planned (see [Status](#status)).

## Architecture Overview
<img width="831" height="581" alt="image" src="https://github.com/user-attachments/assets/8defb40f-0eec-4b05-adb4-253ee0c14ff4" />

## Key Features
- **Stateful Routing (in progress):** the graph is a LangGraph `StateGraph` with typed state, but its edges are a straight line today. Conditional edges for clinical policy evaluation are planned.
- **Observability & Evals:** With a LangSmith key set, every run is traced, and the evals score each model as an experiment: citation resolution against ground truth, decision accuracy, tokens and estimated cost. Per-node timings are captured, and `evals/run.py` prints them, but experiment summaries do not report latency yet.
- **Deterministic Guardrails:** they fail closed. A model reply that does not match the schema raises and the run stops; there is no retry. A tool error in `extract` stops the run. In `verify`, a citation that cannot be fetched counts as not found and its assertion is flagged. The one fallback is the provider's: on Claude Opus 5 and Fable, a refusal is re-run server-side on another model.

**Why prior auth.** It's the highest-friction administrative workflow in US healthcare, it has an unambiguous ROI story (denial rate, A/R days), CMS electronic PA requirements tighten through 2027, and it structurally requires a human approver — which makes it the right shape for demonstrating human-in-the-loop clinical AI.

**Data.** All patient data is Synthea-generated synthetic FHIR R4 (a fixed-seed cohort of adults with rheumatoid arthritis). No PHI touches this repo, which means no BAA, and no de-identification review.

## Tech Stack
- LangGraph / LangChain
- LangSmith
- Python / Asyncio / FastAPI
- MCP (FastMCP) for chart access
- Presidio for pattern-based de-identification

## Status

Built:
- MCP server with read-only FHIR tools over the Synthea bundles (`api/app/mcp/`)
- The PHI boundary: gateway, vault, date shifting, rehydration and the outbound prompt guard (`api/app/phi/`, `api/app/llm/guard.py`)
- The graph, `extract → assemble → verify`, and `run_pipeline`, the one runner for a case (`api/app/graph/`)
- Anthropic and Gemini clients on LangChain chat models (`api/app/llm/langchain_client.py`)
- Metadata-only LangSmith tracing (`api/app/observability.py`)
- Evals: one-patient runs, LangSmith experiments, the citation resolution metric and recorded results (`evals/`)

Planned:
- Conditional routing in the graph
- API routes to create and fetch a case, stream node events and record a review (`api/app/routes/`, stubs). The FastAPI app serves only a hello-world endpoint today.
- Reviewer dashboard: case queue, packet view, citation trace, approve · edit · reject (`web/`, placeholder pages)
- Audit trail of reviewer decisions (`api/app/audit/log.py`, a stub)
- Eval gate on PRs (`.github/workflows/evals.yml`, a placeholder step)
- Latency in experiment summaries

### The PHI boundary

Every tool result from the chart is de-identified in the PHI gateway (`phi/gateway.py`) before the graph sees it, and the placeholder mapping is kept in an in-memory vault made for one run. Structured fields are handled field by field, and a field with no rule raises rather than passing through. Free text goes through Presidio, but without NER: names are matched against a deny-list learned from the patient's structured records, and Presidio's pattern recognizers catch phone numbers, email addresses and SSNs. NER is off on purpose: on the cohort's notes, spaCy missed the patient's first name in 100 of 100 notes and flagged clinical text as names and places. Dates are shifted by a per-patient offset, not redacted, so durations still compute. Before any prompt leaves, a guard (`llm/guard.py`) refuses it if it holds a value the vault knows to be real.

The model responds referencing placeholders; `run_pipeline` rehydrates the packet and the verdicts in its return value, the copy meant for the reviewer. The vault is never written to disk and is cleared when the run ends.

This is the design decision worth arguing about, and the six questions it exists to answer:

| Question                           | Answer in this system                                                    |
| ---------------------------------- | ------------------------------------------------------------------------ |
| Where does inference run?          | Third-party API, which is exactly why the scrub layer exists             |
| Is anything retained for training? | The payload is de-identified regardless. Zero data retention is a setting on the provider account; nothing in this code configures it |
| How is context minimized?          | `extract` scopes retrieval to the criteria; the full chart is never sent |
| What's logged, and where?          | Metadata-only spans; prompt capture is opt-in; the re-ID map never logged |
| How is output attributed?          | An evidence assertion cites records by `resource_type` and id: a Condition, MedicationRequest, Observation or DocumentReference. `verify` flags one with no citations. Showing them in the UI is planned |
| What's the audit trail?            | Planned: reviewer decision, timestamp, and diff against the draft        |

### The graph

Three nodes. Deliberately three. They are plain async functions wired into a LangGraph `StateGraph` in `graph/build.py`, in a line: `extract → assemble → verify`. `run_pipeline` in the same module is the one way a case is run, by the evals today and by the API once its routes are built: it makes the per-run vault, PHI gateway and prompt guard, and reports progress as events that carry metadata only.

- **`extract`** — for each criterion in the payer's criteria file, pulls the chart evidence bearing on it via MCP FHIR tools: records filtered by code, and keyword-matching lines from the most recent notes
- **`assemble`** — drafts the packet against the payer's criteria for that service
- **`verify`** — resolves each generated assertion back to a record in the chart; unsupported claims are flagged for the reviewer, never silently dropped

### Evaluation

The eval dataset runs as LangSmith experiments (`evals/experiment.py`), one per model, scored against Synthea ground truth. There is no LLM-judged faithfulness metric in the MVP: it would add a model call (and cost) to every run, and `verify` already checks what a payer would reject. Known limit: nothing checks that an assertion's *wording* is faithful to the source it cites, only that the citation exists and bears on the criterion.

The metric that matters here is **citation resolution rate**: of the assertions the agent makes in a packet, what fraction point at a record that exists _and_ actually supports the claim. It's domain-specific, it's the thing a payer would reject the packet over, and it's scored against Synthea ground truth rather than an LLM judge.

LangSmith collects runtime traces out-of-band: one run per graph node with counts, criterion ids, the model that answered, and token and cost figures. LangGraph and LangChain would trace every run's whole inputs and outputs on their own, so the LangSmith client used here drops everything but an allowlist of keys, and only what we write ourselves (run names, timings, metadata) is sent. Nothing that sees raw data (the MCP client or server, HTTP clients, request bodies) is traced, and a test enforces that. Recording states, prompts and responses in full (de-identified) is opt-in (`LANGSMITH_CAPTURE_CONTENT`), because LangSmith retains what it stores. Don't set `LANGSMITH_TRACING`: the app turns tracing on itself, through that client.

---

## Repo structure

```
medical-necessity/
├── README.md
├── docker-compose.yml
├── .env.example
│
├── api/                           # FastAPI application
│   ├── pyproject.toml
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── observability.py       # LangSmith client with a metadata-only allowlist
│   │   ├── routes/                # planned; stubs today
│   │   │   ├── cases.py           # create / fetch a prior auth case
│   │   │   ├── stream.py          # SSE node events to the dashboard
│   │   │   └── review.py          # approve · edit · reject
│   │   ├── graph/
│   │   │   ├── build.py           # LangGraph StateGraph wiring + run_pipeline
│   │   │   ├── state.py           # typed graph state
│   │   │   ├── criteria.py        # loads the payer criteria YAML
│   │   │   ├── support.py         # whether a record bears on a criterion
│   │   │   └── nodes/
│   │   │       ├── extract.py
│   │   │       ├── assemble.py
│   │   │       └── verify.py
│   │   ├── phi/
│   │   │   ├── gateway.py         # the only path to the chart; scrubs every tool result
│   │   │   ├── anonymize.py       # field rules, deny-list names, Presidio patterns
│   │   │   ├── dates.py           # per-patient date shifting
│   │   │   ├── vault.py           # per-run re-ID map, TTL
│   │   │   └── rehydrate.py
│   │   ├── llm/
│   │   │   ├── client.py          # provider-agnostic interface + factory
│   │   │   ├── langchain_client.py  # Anthropic and Gemini via LangChain chat models
│   │   │   ├── guard.py           # refuses any prompt holding a value the vault knows
│   │   │   └── prompts/
│   │   ├── mcp/
│   │   │   ├── server.py          # FastMCP server (streamable HTTP, :8001)
│   │   │   ├── tools.py           # FHIR read tools exposed to the agent
│   │   │   ├── models.py          # typed tool results; id + resource_type = the citation
│   │   │   └── store.py           # lazy per-patient loader over the Synthea bundles
│   │   └── audit/
│   │       └── log.py             # planned; stub today
│   └── tests/
│
├── data/
│   ├── synthea/                   # generated FHIR R4 bundles (gitignored)
│   ├── generate.sh                # Synthea invocation + seed, keeps RA patients
│   └── payer_criteria/
│       └── adalimumab_ra.yaml     # one service line, hardcoded on purpose
│
├── evals/
│   ├── dataset.py                 # cases + ground truth from Synthea
│   ├── run.py                     # one patient, end to end, scored
│   ├── experiment.py              # the dataset as LangSmith experiments, one per model
│   ├── metrics/
│   │   └── citation_resolution.py
│   ├── test_*.py                  # harness tests against scripted models
│   └── RESULTS.md                 # scores across both providers
│
├── web/                           # Next.js reviewer dashboard (planned; placeholder pages)
│   ├── app/
│   │   ├── page.tsx               # case queue
│   │   └── cases/[id]/page.tsx
│   └── components/
│       ├── PacketView.tsx
│       ├── CitationTrace.tsx      # assertion → source record
│       └── ReviewActions.tsx
│
└── .github/workflows/
    └── evals.yml                  # eval gate on PR (planned; placeholder step)
```

---

## Running it

```bash
cp .env.example .env               # set LLM_PROVIDER (anthropic | gemini) and its key
./data/generate.sh                 # Synthea → FHIR R4 bundles (needs Docker; ~30 min for 30 patients)
docker compose up
```

Dashboard on `:3000`, API on `:8000`, MCP server on `:8001`. The dashboard and API are placeholders today (see [Status](#status)); the MCP server serves the chart tools.

The evals run on the host, from the repo root, with the API package installed:

```bash
pip install -e 'api[dev]'
python evals/experiment.py                       # the eval: the dataset on both configured models, as LangSmith experiments
python evals/run.py --patient Loyd638 --provider both   # one patient, end to end
cd evals && pytest                               # harness tests against scripted models; no model calls
```

`experiment.py` and `run.py` call real models, so they need the provider keys in `.env`. Without a `LANGSMITH_API_KEY` they still run and score locally; nothing is uploaded.

---

## Deliberately out of scope

Named so the omissions read as decisions rather than gaps:

- **No auth, no multi-tenancy.** Single-reviewer demo.
- **No real payer integration.** No X12 278 generation, no clearinghouse. One payer's criteria, in YAML.
- **No real EHR.** Synthea only. A real deployment substitutes a SMART on FHIR backend service client behind the same MCP interface — which is the point of putting MCP there.
- **No write-back.** The packet leaves as a document, not an order.
