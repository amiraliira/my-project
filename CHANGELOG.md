# Changelog

## rc13 - delivery revision 2 (2026-11-22)

- Refine the management layout and preserve the five work categories plus the combined chart.
- Open an individual's completed activity by hall within the selected performance period.
- Improve supervisor grade entry and decision guidance for eligible and legacy reports.
- Review PM plans and issued responsibility before applying explicit audited changes. Support new PM forms for configured units.
- Load historical activity reports when opened or requested. Refresh cached frontend assets with the new delivery.
- Use the same API version, core schema and OEE module as rc13 revision 1. Use the deployment guide to update from rc12.

## rc13 - delivery revision 1 (2026-11-21)

- Compact the management page and put secondary information behind expandable controls. Show recognizable report details in place of generated identifiers.
- Add separate Corrective Maintenance (CM) and hall-level Assigned Work categories to work registration. Existing project records retain their category.
- Report completed work across all categories or one chosen category, grouped by hall or line. Open a chart bar to review the work, participants and consumed parts.
- Use API 8 and core schema 12 with explicit owner migration 11-to-12. Optional OEE remains module version 1.

## rc12 - delivery revision 4 (2026-11-16)

- Separate PM technical classification from the responsible execution team. Head/manager can correct future plan ownership and untouched issued routing; the assigned team can execute, review and archive the form.
- Add the explicit core-schema 10-to-11 guard upgrade, preserving existing business rows, passwords, issued identities, archives and OEE v1 data.
- Add explicit read-only database/OEE diagnosis; distinguish missing modules, partial installations and runtime grants without automatic activation.
- Move configuration implementation into the installable Python package; retain the existing ASGI entry point and `app.settings` compatibility import.
- Show the selected configuration file, exported override names and effective database login in local preflight. Report controlled configuration errors without printing passwords.
- Consolidate installation and update instructions into one README per area. Archive superseded handoff guides and historical evidence outside the application delivery.
- Remove the obsolete TLS/TOML shell launcher and `/opt` deployment example from the manual Uvicorn workflow.
- Correct source packaging to use the current release metadata, include `.env.example`, exclude private `backend.env` and runtime files, and verify exact archive contents.
- Keep generated benchmark reports under `.build/reports/`, outside source documentation.

Compatibility: backend `2.0.0rc12`, frontend `2.0.0-rc.12`, API 7, core schema 11 and optional OEE 1. A core-10 installation requires the explicit 10-to-11 guard upgrade and matching UI. Preserve the working configuration, database, PM originals and approved scheduling/backup settings.
