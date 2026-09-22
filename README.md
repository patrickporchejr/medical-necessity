# Medical Necessity AI Engine

An open-source clinical evaluation and stateful routing engine built with **LangGraph** and **LangSmith** to automate prior authorization and medical necessity checks.

The agent reads a synthetic patient chart over FHIR, matches the requested service against payer criteria, drafts the medical-necessity packet, and verifies that every assertion in that packet resolves to a real document in the chart. A human reviewer approves, edits, or rejects.

## Architecture Overview
<img width="831" height="581" alt="image" src="https://github.com/user-attachments/assets/8defb40f-0eec-4b05-adb4-253ee0c14ff4" />

## Key Features
- **Stateful Routing:** LangGraph conditional edges for clinical policy evaluation.
- **Observability & Evals:** LangSmith integration for tracking context grounding, accuracy, and latency.
- **Deterministic Guardrails:** Fallbacks for schema validation and tool errors.

**Why prior auth.** It's the highest-friction administrative workflow in US healthcare, it has an unambiguous ROI story (denial rate, A/R days), CMS electronic PA requirements tighten through 2027, and it structurally requires a human approver — which makes it the right shape for demonstrating human-in-the-loop clinical AI.

**Data.** All patient data is Synthea-generated synthetic FHIR R4 (a fixed-seed cohort of adults with rheumatoid arthritis). No PHI touches this repo, which means no BAA, and no de-identification review.

## Tech Stack
- LangGraph / LangChain
- LangSmith
- Python / Asyncio / FastAPI

### The PHI boundary

Presidio de-identifies every outbound prompt and stores the placeholder mapping in a request-scoped in-memory vault. The model responds referencing placeholders; the response is rehydrated before it reaches the reviewer. The vault is never written to disk and dies with the request.

This is the design decision worth arguing about, and the six questions it exists to answer:

| Question                           | Answer in this system                                                    |
| ---------------------------------- | ------------------------------------------------------------------------ |
| Where does inference run?          | Third-party API, which is exactly why the scrub layer exists             |
| Is anything retained for training? | No — zero-retention mode, and the payload is de-identified regardless    |
| How is context minimized?          | `extract` scopes retrieval to the criteria; the full chart is never sent |
| What's logged, and where?          | Metadata-only spans; prompt capture is opt-in; the re-ID map never logged |
| How is output attributed?          | Every assertion carries a `DocumentReference` id, surfaced in the UI     |
| What's the audit trail?            | Reviewer decision, timestamp, and diff against the draft                 |

### The graph

Three nodes. Deliberately three. They are plain async functions wired into a LangGraph `StateGraph` in `graph/build.py`, in a line: `extract → assemble → verify`. `run_pipeline` in the same module is the one way a case is run, by the API and the evals alike: it makes the per-run vault, PHI gateway and prompt guard, and reports progress as events that carry metadata only. `run_pipeline` in the same module is the one way a case is run, by the API and the evals alike: it makes the per-run vault, PHI gateway and prompt guard, and reports progress as events that carry metadata only.

- **`extract`** — pulls the requested service and the clinical evidence bearing on it via MCP FHIR tools
- **`assemble`** — drafts the packet against the payer's criteria for that service
- **`verify`** — resolves each generated assertion back to a source document; unsupported claims are flagged for the reviewer, never silently dropped

### Evaluation

The eval dataset runs as LangSmith experiments (`evals/experiment.py`), one per model, scored against Synthea ground truth. There is no LLM-judged faithfulness metric in the MVP: it would add a model call (and cost) to every run, and `verify` already checks what a payer would reject. Known limit: nothing checks that an assertion's *wording* is faithful to the source it cites, only that the citation exists and bears on the criterion.

The metric that matters here is **citation resolution rate**: of the assertions the agent makes in a packet, what fraction point at a document that exists _and_ actually supports the claim. It's domain-specific, it's the thing a payer would reject the packet over, and it's scored against Synthea ground truth rather than an LLM judge.

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
│   │   ├── routes/
│   │   │   ├── cases.py           # create / fetch a prior auth case
│   │   │   ├── stream.py          # SSE node events to the dashboard
│   │   │   └── review.py          # approve · edit · reject
│   │   ├── graph/
│   │   │   ├── build.py           # LangGraph StateGraph wiring
│   │   │   ├── state.py           # typed graph state
│   │   │   └── nodes/
│   │   │       ├── extract.py
│   │   │       ├── assemble.py
│   │   │       └── verify.py
│   │   ├── phi/
│   │   │   ├── anonymize.py       # Presidio analyzer + operators
│   │   │   ├── vault.py           # request-scoped re-ID map, TTL
│   │   │   └── rehydrate.py
│   │   ├── llm/
│   │   │   ├── client.py          # provider-agnostic interface + factory
│   │   │   ├── anthropic_client.py
│   │   │   ├── gemini_client.py
│   │   │   ├── guard.py           # refuses any prompt holding a value the vault knows
│   │   │   └── prompts/
│   │   ├── mcp/
│   │   │   ├── server.py          # FastMCP server (streamable HTTP, :8001)
│   │   │   ├── tools.py           # FHIR read tools exposed to the agent
│   │   │   ├── models.py          # typed tool results; id + resource_type = the citation
│   │   │   └── store.py           # lazy per-patient loader over the Synthea bundles
│   │   └── audit/
│   │       └── log.py
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
│   ├── metrics/
│   │   └── citation_resolution.py
│   └── RESULTS.md                 # scores across both providers
│
├── web/                           # Next.js reviewer dashboard
│   ├── app/
│   │   ├── page.tsx               # case queue
│   │   └── cases/[id]/page.tsx
│   └── components/
│       ├── PacketView.tsx
│       ├── CitationTrace.tsx      # assertion → source document
│       └── ReviewActions.tsx
│
└── .github/workflows/
    └── evals.yml                  # eval gate on PR
```

---

## Running it

```bash
cp .env.example .env               # set LLM_PROVIDER (anthropic | gemini) and its key
./data/generate.sh                 # Synthea → FHIR R4 bundles (needs Docker; ~30 min for 30 patients)
docker compose up
```

Dashboard on `:3000`, API on `:8000`, MCP server on `:8001`.

```bash
cd evals && pytest                 # run the eval suite
```

---

## Deliberately out of scope

Named so the omissions read as decisions rather than gaps:

- **No auth, no multi-tenancy.** Single-reviewer demo.
- **No real payer integration.** No X12 278 generation, no clearinghouse. One payer's criteria, in YAML.
- **No real EHR.** Synthea only. A real deployment substitutes a SMART on FHIR backend service client behind the same MCP interface — which is the point of putting MCP there.
- **No write-back.** The packet leaves as a document, not an order.
