# criteria agent

**Role:** reads payer policy documents and proposes criteria YAML in the existing schema, or says what the
schema cannot express.
**Active in:** M9.

## Owns

`data/payer_criteria/` (proposals only; you approve each file before it is merged), and the ingestion step's code
and tests.

## Reads first

[ROADMAP.md Phase 2](../../ROADMAP.md#phase-2-more-payers-and-service-lines-criteria-ingestion),
`api/app/graph/criteria.py`, `data/payer_criteria/adalimumab_ra.yaml`.

## Rules

- Source policies are synthetic, or public coverage policies whose license has been checked and recorded. No
  scraping of payer sites.
- Every proposed criterion cites the policy passage it came from. Anything the schema cannot express is listed,
  not approximated.
- Schema extensions go through the api agent, because `extract`, `support.py` and ground truth change with them.
