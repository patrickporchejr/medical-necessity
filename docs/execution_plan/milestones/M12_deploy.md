# M12: deploy on AWS

**Source:** [ROADMAP.md](../../ROADMAP.md#phase-6-deployment-on-aws) Phase 6. **Agents:** infra, qa.
**Checkpoints:** CP12a (before anything is provisioned), CP12b (live). May start after CP7.

Planned to the outcome; tasks written at CP7.

- Production images for api, mcp, lab and web; the Synthea bundles and lab file on storage the servers read.
- Managed containers, Postgres, a secrets manager, central logs; LangSmith allowlist unchanged.
- Access gate or shared passcode, rate limits, a hard monthly model spend cap; provider zero retention stated.
- **CP12a:** architecture, monthly cost of leaving it up, cap, gate, checked against current AWS pricing. Nothing
  is provisioned before your yes.
- **CP12b:** a stranger opens the demo and runs a batch within the cap; the cost figure is in the repo.
