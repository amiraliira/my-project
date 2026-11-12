# Documentation index

Start with the [project README](../README.md). The following READMEs are the current operational instructions. Each section states where to run commands, what to preserve and what successful checks look like.

| Task | Single section entry point |
| --- | --- |
| Recover a complete backend, configure it and run Uvicorn | [Backend README](../backend/README.md) |
| Inspect the actual database, back up and upgrade core schema | [Database README](../backend/schema/README.md) |
| Enable and verify the optional OEE module | [OEE README](../backend/oee/README.md) |
| Locate and preserve PM originals, runtime settings and backups | [Runtime-data README](runtime-data/README.md) |
| Build the frontend and resolve missing-source errors | [Frontend README](../frontend/README.md) |
| Publish the matching UI and verify Nginx API routing | [Deployment README](../deploy/README.md) |

## Feature references

These explain application behavior rather than installation order:

- [Recorded EM downtime and clickable line details](em-downtime-fa.md).
- [Unified interface, 24-hour entry and supervisor reporting](rc12-ui-fa.md).
- [Architecture reference](architecture.md).
- [CIP calculation change](cip-backend-fix.md).

## Historical guides and review evidence

Older `update-rc11-fa.md`, `update-rc12-fa.md`, manual deployment notes and prior handoff text describe the release work at their writing date. Do not combine their old setup commands with the current section READMEs. Use the table above for this package's manual update procedure.

Files named `*-verification.json`, `verification/`, release-review notes and screenshots retain test and review evidence. They describe the tested local fixtures and recorded limitations. They do not prove what is currently installed, which passwords are active, or which data has since been entered on the company server.

The application version, core schema version and optional OEE module version are separate values. Read the database state and backend health response as instructed; an rc10 label in a cached browser screen is not a schema inspection.
