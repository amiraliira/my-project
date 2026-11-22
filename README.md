# PAK Maintenance

PAK **2.0.0-rc.13, delivery revision 2** adds a redesigned management workspace, person-to-hall activity review, and PM responsibility review. It includes the five work categories and combined activity report from revision 1.

## Choose your task

| Task | One authoritative guide |
| --- | --- |
| Update the currently working installation | [Deployment](deploy/README.md#update-the-working-installation) |
| Install on a prepared Linux host using a verified PAK backup | [New host](deploy/README.md#install-on-a-new-host-from-a-verified-backup) |
| Configure, check and run FastAPI manually | [Backend](backend/README.md) |
| Back up, inspect or explicitly upgrade the database | [Database](backend/schema/README.md) |
| Enable the optional OEE module | [OEE](backend/oee/README.md) |
| Build or troubleshoot the frontend | [Frontend](frontend/README.md) |
| Locate PM originals, private settings and backups | [Runtime data](docs/runtime-data/README.md) |
| Understand the application and development process | [Documentation](docs/README.md) |

Start with the [deployment guide](deploy/README.md#update-the-working-installation). After a verified backup, **core schema 11** requires **11-to-12**. **Core schema 10** requires **10-to-11**, then **11-to-12**. Install the matching backend and supplied frontend build after the database checks pass. The update preserves existing reports and their categories, including projects. Keep the working private settings, PM originals and any installed OEE v1 module.

## Version contract

| Component | Contract |
| --- | --- |
| Backend application | `2.0.0rc13` |
| Frontend application | `2.0.0-rc.13` |
| API | `8`, including personnel activity drilldown and PM routing review |
| Core schema | **12** |
| Optional OEE module | **1** |
| PostgreSQL | 17 or newer |

These are separate version numbers. A page label does not identify the database schema. Startup verifies storage and runtime privileges; it never creates or migrates the database.

## Source and runtime locations

The delivery folder is `PAK-Project/`. The examples preserve the working Linux installation's existing locations:

```text
~/Desktop/
  PAK-Project/              extracted release to verify and install
    README.md
    backend/               FastAPI entry point, package, schemas and tests
    frontend/              React source and generated dist/
    deploy/                this update procedure and Nginx examples
    docs/                  current product and developer references
    tools/                 release verification helpers
  backend/                 active backend source
  venv/                    existing Python environment
  UI/                      existing Nginx document root
  PAK-data/                private runtime data, outside the release
    backend.env
    seed/
    backups/
    logs/
```

If the installation is elsewhere, substitute the actual paths consistently. Keep the existing PostgreSQL database, password hashes, private `backend.env` and PM seed; source updates do not replace them. The release contains no real credentials, live database or replacement seed dataset.

## Verify a complete delivery

From the extracted release root, before editing it:

```sh
cd "$HOME/Desktop/PAK-Project"
python3 -B tools/verify_source.py
```

Expect JSON with `successful: true` and `handoffRevision: 2`. If verification reports a missing file or checksum mismatch, extract a fresh copy of the full archive.

Transfer the complete project using Git or the complete ZIP. A browser file-upload page is not a reliable way to transfer a whole project in one operation. Preserve exact filenames, including `__init__.py`, `.gitignore` and `.env.example`. Keep a single `README.md` spelling in each directory.

See [feature reference](docs/features.md) for a description of the report categories and the activity chart.
