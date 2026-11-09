# Linux deployment from source — rc10

This is the complete development source handoff, not the Windows portable installer. The backend uses FastAPI and PostgreSQL; the frontend uses React, TypeScript, Vite and Tailwind. No company database, PM originals, usable passwords, certificates or private keys are included.

The rc10 functional, native PostgreSQL and transport test evidence was produced on Windows with Python 3.14.8. This reorganized source delivery does **not** establish Linux deployment acceptance. IT must complete the checks below on the actual Linux host before use.

## Prerequisites

- Use an approved Python 3.14 installation and a virtual environment. The project declares Python 3.12 or later; the recorded runtime baseline is 3.14.8, not an independently tested matrix of all supported Python versions.
- Frontend build: Node.js 22.12 or later and an approved pnpm installation supporting lockfile version 9. The cached Vite 7.3.6 and React plugin declare Node `^20.19.0 || >=22.12.0`; Node 22.12+ is the straightforward choice for this handoff.
- PostgreSQL 17 or later, with a separate structure owner and limited runtime login. For the existing `FanipakDB` PostgreSQL 18.6 container, use compatible PostgreSQL 18 or newer client tools for backup.
- The **backend environment** must have real `pg_dump` and `pg_restore` executable files. A binary present only inside the database container is not a host path. `/usr/bin/docker` is not a replacement for either executable. Typical package-managed Debian paths are `/usr/lib/postgresql/18/bin/pg_dump` and `/usr/lib/postgresql/18/bin/pg_restore`; IT must verify its actual installation paths and versions.
- IT supplies the approved Linux wheels, pnpm store, restored data, PM originals and internal TLS material. Windows wheels and the Windows portable Python runtime cannot be reused as Linux binaries.

## Install and build without downloads

Run from the extracted source root. Replace `/srv/pak-wheelhouse` with the prepared wheel directory for this Linux architecture and Python version:

```bash
python3.14 -m venv .venv
. .venv/bin/activate
python -m pip install --no-index --find-links /srv/pak-wheelhouse \
  -r backend/requirements.txt
python -m pip check

cd frontend
pnpm install --offline --frozen-lockfile
pnpm test
pnpm build
cd ..
```

`backend/requirements.txt` pins the 17 runtime dependency versions used by rc10. It is a version pin list, **not** a Linux artifact-hash lock. The old reviewed Windows artifact lock is retained at `backend/locks/requirements-windows-cp314.lock`; do not install its Windows wheel hashes on Linux. IT should record the approved Linux wheel hashes and installation output. Offline installation deliberately fails if the required wheel or pnpm cache entry is missing.

The repository also contains `frontend/dist` for reviewing the delivered build. `frontend/src`, `frontend/public`, the TypeScript/Vite configuration and `pnpm-lock.yaml` are the editable build inputs. `node_modules` is deliberately excluded.

## Prepare data and configuration

1. Restore the approved native `.dump` into a new, empty dedicated database as the structure owner, using `pg_restore --no-owner --no-privileges --exit-on-error --single-transaction`. The dump and the target container are separate from this source archive. Do not restore into the administrative `postgres` database or delete an existing working database.
2. Provision a separate runtime login with CONNECT, schema USAGE, table SELECT/INSERT/UPDATE/DELETE, required sequence access and function EXECUTE. It must not own tables, belong to the owner role, have schema CREATE, TRUNCATE/TRIGGER or administrative privileges. The backend verifies these boundaries when starting; it does not grant privileges or create initial login accounts automatically.
3. Transfer the approved `pm-import.json` and `pm-originals/` together into the chosen local seed directory. A database restore alone does not transfer the original PM documents. Preserve any completed-form evidence required by the data handoff.
4. For a first installation, use `deploy/linux/server.example.toml` as the template for a protected local `server.toml`. Preserve an existing working configuration during updates. Enter the real numeric internal listener/database IPs, runtime credentials and absolute Linux paths. `frontend_directory` must identify the deployed `frontend/dist`, not `frontend/src`.
5. Production requires HTTPS and database TLS `sslmode = "verify-full"` with an explicit local CA. The database server certificate must match the numeric database IP; the HTTPS certificate must match the numeric listener IP. Container DNS names such as `FanipakDB`, wildcard listener addresses and disabled production TLS are rejected by this application's configuration policy.
6. Keep `development = false`. Leave PM issuance paused until the approved final start decision; `scheduling_enabled = false` is appropriate during migration acceptance. An existing database's PM activation state must be checked separately. Normal startup never resets PM, seeds accounts or migrates schema.

The core database contract is schema 8. OEE is a separately enabled schema-1 add-on. If OEE was active in the restored source, its structures and data travel with that dump. Do not apply a schema script or add-on migration blindly to an already restored database.

## Start the application

Activate the prepared virtual environment, then run the root launcher with three absolute paths:

```bash
. .venv/bin/activate
bash tools/run_backend.sh \
  /opt/pak/shared/server.toml \
  /opt/pak/shared/certificates/server.crt \
  /opt/pak/shared/certificates/server.key
```

The launcher selects `backend/src` through `PYTHONPATH` and calls the real CLI. The equivalent command from the source root is:

```bash
PYTHONPATH="$PWD/backend/src" python -m pak_maintenance \
  --config /opt/pak/shared/server.toml \
  --certificate /opt/pak/shared/certificates/server.crt \
  --private-key /opt/pak/shared/certificates/server.key
```

There is no module-level `app` object for an invented `uvicorn app:app` command. The application is constructed from validated configuration. Its CLI starts one Uvicorn process and serves the compiled frontend and API from the same HTTPS origin. Use the configured internal address in a browser. A Vite development or preview server is not the production application server.

## Linux acceptance and later updates

IT must verify startup, real login, role visibility, one report through supervisor/head approval, individual scores, PM assignment and archive visibility, OEE where enabled, and an actual backup/restore into a separate test database. Compare restored record counts and content with the supplied data manifest; do not treat a successful archive listing as a restore test. Record the Linux/Python/libpq/PostgreSQL versions and any skipped tests.

The included backend tests are source tests, not an unrestricted production test command. Native integration tests require their explicitly isolated disposable QA configuration. The native QA path helper selects the executable suffix for its actual operating system, but its Linux execution has not been validated. Retained personal-launcher and Windows release tests are platform-specific and must not be presented as Linux test results. Never point destructive QA fixtures at the working database.

The existing `backend/tools/package_portable.py`, `.cmd` launchers and managed release-controller workflow target Windows. They are retained as release tooling references, not a tested Linux installer or Linux update service. For Linux, stage the next approved source/build in a separate release directory, stop the app, obtain a verified data/configuration backup, preserve shared data and secrets, and validate the new version before switching the supervised process. Any database change needs its own reviewed migration and recovery procedure; copying source does not migrate the database.

The source application does not install services, alter the company firewall, manage Docker networking or configure host-wide inbound/outbound policies. Those remain IT deployment responsibilities.
