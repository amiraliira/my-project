# Backend

[Project README](../README.md) | [Database](schema/README.md) | [OEE](oee/README.md) | [Runtime data](../docs/runtime-data/README.md)

Normal FastAPI, started manually with Uvicorn. Use one complete backend release. This release requires **API 7 / core schema 10**, with optional **OEE module 1**. Source code, Python environment, private settings and the database are separate.

## 1. Verify the source

Extract beside the old installation, not over it:

```sh
cd "$HOME/Desktop/PAK-rc12"
python3 -B tools/verify_source.py
```

Expect `"successful": true` and `"handoffRevision": 3`. This verifies the source package, not the database.

```text
backend/
  app/
    __init__.py             # Selects this release's domain package
    main.py                 # app.main:app Uvicorn entry point
    settings.py             # Private .env parsing
  src/pak_maintenance/
    __init__.py             # Defines __version__; must not be empty
    application.py          # FastAPI factory and startup validation
    api.py                  # API routes and capabilities
  schema/README.md          # Database preservation and upgrades
  oee/README.md             # Optional OEE activation
  tools/check_installation.py
  requirements.txt
  pyproject.toml
```

Preserve both `__init__.py` files. Do not flatten directories or copy only selected Python files. `src/pak_maintenance/` contains business source, not runtime data.

## 2. Prepare the Python environment

Use installed Python 3.12 or newer. Create this dedicated environment once and reuse it; keep the old environment available for rollback:

```sh
python3 --version
python3 -m venv "$HOME/Desktop/PAK-venv-rc12"
source "$HOME/Desktop/PAK-venv-rc12/bin/activate"
cd "$HOME/Desktop/PAK-rc12/backend"
python -m pip install -r requirements.txt
python -m pip check
python -B tools/check_installation.py
```

Expect no broken pip requirements and preflight showing `2.0.0rc12`, this release's `src/pak_maintenance/__init__.py`, and `[OK] Local preflight passed`. Preflight reads local files and installed metadata; it never connects to PostgreSQL.

## 3. Preserve and select the configuration

Reuse the **currently active** private configuration. Do not replace it with `.env.example`. If the old process used `PAK_ENV_FILE`, that selected file takes precedence over its backend `.env`; identify the correct file first. Never share or upload its contents.

To centralize a confirmed active file from the reported old location, run this **once**:

```sh
mkdir -p "$HOME/Desktop/PAK-data"
chmod 700 "$HOME/Desktop/PAK-data"
test ! -e "$HOME/Desktop/PAK-data/backend.env" && install -m 600 "$HOME/Desktop/backend/.env" "$HOME/Desktop/PAK-data/backend.env"
```

If `backend.env` already exists the last command intentionally does not replace it. Review that existing file instead. If the confirmed active file is elsewhere, use its actual path as the copy source. The old file stays intact for rollback.

Select the centralized configuration in every new shell before Uvicorn:

```sh
export PAK_ENV_FILE="$HOME/Desktop/PAK-data/backend.env"
nano "$PAK_ENV_FILE"
```

For a genuinely new installation only, copy `.env.example` to a private file and fill real values. The existing installation must keep its actual database password and paths.

For the reported same-host Docker/HTTP setup these are the relevant **non-secret** settings; retain the existing `PAK_DB_PASSWORD` privately:

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
PAK_SCHEDULING_ENABLED=false
PAK_AUTOMATIC_BACKUP_ENABLED=false
```

Keep each setting once. Matching outer quotes are removed; values remain literal. No inline comments, shell `export` lines or variable expansion inside the file. Only seed/backup paths expand `~/`. Do not shell-source the file. Exported shell settings override file values; remove stale `PAK_DB_*` exports if they conflict.

`127.0.0.1` is the database address **from the Uvicorn machine**. Another machine requires its own transport profile from `.env.example`. Database role `pak` is unrelated to the website admin account.

Validate without a database connection:

```sh
python -B tools/check_installation.py --config
```

Expect configuration and existing PM paths to pass. An empty seed directory is insufficient: both `pm-import.json` and `pm-originals/` are required. See [runtime data](../docs/runtime-data/README.md).

## 4. Prepare the existing database

Follow the [database README](schema/README.md): stop every writer, make and verify a native backup/restore drill, inspect actual schema, apply only necessary migrations. Schema 8 needs 8-to-9 then 9-to-10; schema 9 needs only 9-to-10; valid schema 10 needs neither. PostgreSQL version 18.6 is not the PAK schema number.

Use limited login `pak` for normal execution; keep owner/admin credentials for explicit maintenance. Startup checks schema and privileges without creating, restoring or migrating the database. Existing website passwords remain in the database.

## 5. Run in the foreground first

With the environment active and `PAK_ENV_FILE` exported:

```sh
cd "$HOME/Desktop/PAK-rc12/backend"
python -m uvicorn app.main:app --host 127.0.0.1 --port 5187 --workers 1 --no-proxy-headers
```

The ordinary `uvicorn app.main:app ...` command is equivalent when it belongs to this environment; `python -m uvicorn` makes the interpreter explicit. Do not use `--reload`. Stop the intended old API first; do not run two writers during migration.

Expect `Application startup complete`. In another terminal, check the reported HTTP host:

```sh
curl --fail --show-error -H 'Host: 192.168.12.92' http://127.0.0.1:5187/api/health
```

The Host header must match your `PAK_PUBLIC_URL`. Expect version `2.0.0rc12`, `apiVersion: 7`, `schemaVersion: 10`, capability `em-downtime-drilldown`. The default localhost Host may be rejected by request policy. Follow [OEE](oee/README.md) separately if required. Stop foreground Uvicorn with Ctrl+C before starting detached.

## 6. Run detached and read logs

With the same environment active:

```sh
export PAK_ENV_FILE="$HOME/Desktop/PAK-data/backend.env"
cd "$HOME/Desktop/PAK-rc12/backend"
mkdir -p "$HOME/Desktop/PAK-data/logs"
nohup python -m uvicorn app.main:app --host 127.0.0.1 --port 5187 --workers 1 --no-proxy-headers > "$HOME/Desktop/PAK-data/logs/uvicorn.log" 2>&1 < /dev/null &
echo $! > "$HOME/Desktop/PAK-data/logs/uvicorn.pid"
tail -n 100 -f "$HOME/Desktop/PAK-data/logs/uvicorn.log"
```

Ctrl+C stops `tail`, not the detached API. Check startup logs immediately; a background PID alone is not success. This command replaces the previous log, so preserve it first if needed. `nohup` does not provide automatic restart after reboot/crash. PM scheduling/backups remain controlled by the private settings.

To stop the recorded process, first inspect:

```sh
ps -p "$(cat "$HOME/Desktop/PAK-data/logs/uvicorn.pid")" -o pid,args
```

Only after confirming this is the intended Uvicorn process:

```sh
kill "$(cat "$HOME/Desktop/PAK-data/logs/uvicorn.pid")"
```

Wait for shutdown before restart. A stale PID can refer to another process; never kill it without checking. Identify legacy processes separately if they were started outside this procedure.

## Troubleshooting

| Error | Meaning and action |
| --- | --- |
| `cannot import name '__version__' from 'pak_maintenance' (unknown location)` | Failure precedes database startup. A missing package `__init__.py` may create a namespace package; wrong/stale source is another possibility. Restore the complete matching backend and check source below. Do not edit the database or create an empty initialization file. |
| `Incomplete PAK backend` / `loaded from another location` | Source is incomplete or shadowed. Verify a fresh complete extraction, correct working directory, environment and fresh process. |
| `No module named app` | Start from this release's `backend/`, not the project root or domain source folder. |
| TLS/loopback error | For `127.0.0.1` with TLS disabled, enable the loopback exception. The LAN exception does not replace it. Check `.env.example`. |
| Runtime role has administrative privileges | Use limited `pak` in runtime settings; follow database permission checks. Do not grant more authority to hide the error. |
| Schema mismatch | Inspect the real schema and follow the database README. Never drop/reinitialize the existing database. |
| Address already in use | Identify and stop the intended old process listening on 5187. Updating files does not stop an old running API. |
| Browser shows rc10, API unknown | The screen identifies the loaded frontend only. Resolve API startup, check health through Nginx, then publish the matching UI and follow cache instructions. |

Safe source diagnostic from `backend/`:

```sh
python -c "import app; import pak_maintenance as p; print('source:', p.__file__); print('version:', getattr(p, '__version__', 'MISSING'))"
```

Expect this release's `src/pak_maintenance/__init__.py` and `2.0.0rc12`. It imports neither `app.main` nor database code. Do not invent a version string in mixed source to bypass this error.

## Maintainer map

`application.py` adapts FastAPI; `api.py` defines routes; `database.py` validates storage/permissions. `work_service.py` and `work_review_correction.py` implement reports/approval; `pm_*.py` implement PM forms, PDFs and responsibility; `parts_import.py` handles warehouse imports; `em_downtime.py` provides recorded downtime drilldown. Maintenance tools never run automatically at startup. Tests use generated data. Older portable Windows tools remain source references, not this manual Linux procedure.
