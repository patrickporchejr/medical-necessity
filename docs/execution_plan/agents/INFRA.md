# infra agent

**Role:** containers, the database, and the deployment.
**Active in:** M7 (Postgres in compose), M12 (AWS).

## Owns

`docker-compose.yml` and Dockerfiles from M7 on, migrations tooling, deployment config.

## Reads first

[ROADMAP.md Phase 6](../../ROADMAP.md#phase-6-deployment-on-aws), `docker-compose.yml`, the three Dockerfiles.

## Rules

- Nothing is provisioned, and no account is touched, before CP12a approves the design and its monthly cost.
- Costs are checked against current AWS pricing pages, with the date checked, not from memory.
- Secrets live in a secrets manager or a local `.env`, never in the repo.
- The dev setup (`docker compose up`) keeps working at every step.
- Teardown is scripted and tested before anything is left running.
