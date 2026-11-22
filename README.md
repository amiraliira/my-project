# Backend

[Start](../README.md) · [Update procedure](../deploy/README.md) · [Database](schema/README.md) · [OEE](oee/README.md) · [Runtime data](../docs/runtime-data/README.md)

This is the authoritative manual FastAPI runbook. Examples use the existing Linux account `pak`, active source `~/Desktop/backend`, Python environment `~/Desktop/venv`, and private configuration `~/Desktop/PAK-data/backend.env`.

## Source structure

```text
backend/
  app/
    __init__.py                  source-package selection and integrity guard
    main.py                      public ASGI entry point: app.main:app
    settings.py                  compatibility import for existing callers
  src/pak_maintenance/
    __init__.py                  application version
    settings.py                  literal configuration parsing and validation
    application.py               FastAPI factory and lifecycle
    api.py                       routes and capabilities
    database.py                  transactions, schema and privilege checks
    ...                          work, PM, parts, performance and OEE services
  schema/README.md                database operations and migration decisions
  oee/README.md                   optional module operations
  tools/check_installation.py     local preflight
  tests/                         generated fixtures and regression tests
  requirements.txt               runtime dependency pins
  pyproject.toml                  Python package metadata
```

Keep this layout intact, including both package initialization files. Do not run from `src/pak_maintenance/`. Architecture and development rules are in the [developer guide](../docs/development/README.md).

## 1. Activate the environment

For the existing installation:

```sh
source "$HOME/Desktop/venv/bin/activate"
cd "$HOME/Desktop/backend"
python --version
python -m pip check
```

Python must be 3.12 or newer. For a new installation only, create the environment once with `python3 -m venv "$HOME/Desktop/venv"`. Install a release's dependency pins when needed:

```sh
python -m pip install -r requirements.txt
python -m pip check
```

Do not recreate a working environment for every restart. Keep the previous environment or its dependency state available during an update.

## 2. Select the private configuration

Use the existing, working `backend.env`. For a new installation only, copy `.env.example` to a private file and fill its values. Never replace working credentials with template placeholders.

In every new terminal that will run the backend:

```sh
export PAK_ENV_FILE="$HOME/Desktop/PAK-data/backend.env"
```

The selected file must exist. If `PAK_ENV_FILE` is unset, the legacy fallback is `backend/.env`; `backend.env` is not discovered automatically. Use one explicit file for this installation.

Configuration precedence is **exported `PAK_*` setting > selected file > code default**. An old terminal export can therefore override a corrected file. The preflight reports the selected source and override names. If stale database exports are present, clear them, keeping `PAK_ENV_FILE`:

```sh
unset PAK_DB_HOST PAK_DB_PORT PAK_DB_NAME PAK_DB_USER PAK_DB_PASSWORD
unset PAK_DB_SSLMODE PAK_DB_SSLROOTCERT PAK_DB_ALLOW_LOOPBACK_PLAINTEXT PAK_DB_ALLOW_LAN_PLAINTEXT PAK_DB_CONNECT_TIMEOUT
```

Clear other exported settings only if the preflight identifies them as unwanted overrides. Do not `source`, execute, or use `set -a` on `backend.env`. Its parser treats values literally; shell evaluation can alter passwords or execute characters inside them.

The reported same-host HTTP/Docker installation uses this non-secret profile. Keep the actual password privately and preserve approved scheduling/backup settings:

```dotenv
PAK_PUBLIC_URL=http://192.168.12.92
PAK_ALLOW_INSECURE_HTTP=true
PAK_DB_HOST=127.0.0.1
PAK_DB_PORT=5432
PAK_DB_NAME=pak_maintenance
PAK_DB_USER=pak
PAK_DB_SSLMODE=disable
PAK_DB_ALLOW_LOOPBACK_PLAINTEXT=true
PAK_DB_ALLOW_LAN_PLAINTEXT=false
PAK_DB_SSLROOTCERT=
PAK_PM_SEED_DIR=~/Desktop/PAK-data/seed
PAK_BACKUP_DIR=~/Desktop/PAK-data/backups
PAK_DEVELOPMENT=false
```

`pak` is the PostgreSQL runtime login, not the website admin account. It must be a limited role. Its password is different from the PostgreSQL maintenance account's password. Review role problems using the [database guide](schema/README.md), rather than weakening startup checks.

## 3. Run the local preflight

```sh
python -B tools/check_installation.py --config
```

Expect source version `2.0.0rc13`, this backend's package path, installed dependency checks, the correct configuration source and valid PM seed paths. Verify core schema **12** before starting this release. Use the [database migration steps](schema/README.md#3-apply-only-the-required-migrations) when the existing schema is older. OEE module **1** is optional.

This command validates local source, dependencies and settings. It does not open the database or prove that the database role/schema is correct. Do not start Uvicorn until this check passes. For missing seed files, use [runtime-data recovery](../docs/runtime-data/README.md); creating an empty `seed/` directory does not install PM documents.

## 4. Start in the foreground

From the same terminal:

```sh
python -m uvicorn app.main:app --host 127.0.0.1 --port 5187 --workers 1 --no-proxy-headers
```

The normal `uvicorn app.main:app ...` command is equivalent if it belongs to the active environment. `python -m uvicorn` makes that interpreter explicit. Use one worker and no `--reload` in this manual server setup. Stop the intended old API before using its port.

Expected: `Application startup complete`. Startup now opens PostgreSQL and checks the actual schema and runtime privileges. It still does not migrate, restore or seed anything.

In a second terminal:

```sh
curl --fail --show-error -H 'Host: 192.168.12.92' http://127.0.0.1:5187/api/health
```

Expect HTTP 200 JSON with `version: 2.0.0rc13`, `apiVersion: 8`, `schemaVersion: 12`, `packageRevision: 2` and capabilities `personnel-activity-drilldown` and `pm-routing-review`. The Host header must match `PAK_PUBLIC_URL`.

## 5. Run detached and inspect logs

After foreground startup and health pass, stop that foreground process with Ctrl+C. In the environment above:

```sh
mkdir -p "$HOME/Desktop/PAK-data/logs"
nohup python -m uvicorn app.main:app --host 127.0.0.1 --port 5187 --workers 1 --no-proxy-headers > "$HOME/Desktop/PAK-data/logs/uvicorn.log" 2>&1 < /dev/null &
echo $! > "$HOME/Desktop/PAK-data/logs/uvicorn.pid"
tail -n 100 -f "$HOME/Desktop/PAK-data/logs/uvicorn.log"
```

`PAK_ENV_FILE` must still be exported in this terminal. A printed PID alone is not a successful startup. The command replaces the previous log; preserve that log first if needed. Ctrl+C stops `tail`, not the detached backend. `nohup` does not restart the application after a crash or reboot.

To stop a process started by this procedure, inspect the recorded PID first:

```sh
ps -p "$(cat "$HOME/Desktop/PAK-data/logs/uvicorn.pid")" -o pid,args
```

Only when it is the intended Uvicorn process:

```sh
kill "$(cat "$HOME/Desktop/PAK-data/logs/uvicorn.pid")"
```

Wait for shutdown before restarting. Identify older processes separately; a stale PID can belong to something else.

## Troubleshooting

| Message or observation | Correct action |
| --- | --- |
| Missing `__version__` / missing package initialization | Restore a complete matching backend, including `src/pak_maintenance/__init__.py`; do not create an empty file or invent a version string. |
| `No module named app` | Change directory to the active `backend/`. |
| `Set PAK_DB_NAME` or another required field | Check the preflight's selected configuration path and overrides. Do not assume `backend.env` was read without `PAK_ENV_FILE`. |
| A corrected file still results in the wrong database user | Remove the stale exported database settings shown in step 2, then rerun preflight in the same shell. |
| Runtime account has administrative/replication/bypass authority | Inspect the limited runtime role and ownership using the database guide. A version marker passing does not prove every schema check has passed. |
| TLS or loopback allowance error | Match the transport profile to the address from the Uvicorn machine. Disabled TLS on `127.0.0.1` requires the explicit loopback allowance; a LAN allowance is separate. |
| PM metadata or originals missing | Preserve/copy the actual seed files and correct their parent directory; do not stage/import PM again. |
| Schema mismatch | Inspect the actual contract. Use the [owner-run upgrade steps](schema/README.md#3-apply-only-the-required-migrations) for the inspected schema after a verified backup. |
| Address already in use | Identify the process listening on 5187 and stop the intended old API. |
| Browser reports an unknown backend version | Finish foreground startup and direct health first, then check the [Nginx route](../deploy/README.md). |

Safe package diagnostic, without importing settings or connecting to PostgreSQL:

```sh
python -c "import app; import pak_maintenance as p; print('source:', p.__file__); print('version:', p.__version__)"
```
