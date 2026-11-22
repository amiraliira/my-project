# Deployment

[Start](../README.md) · [Backend runbook](../backend/README.md) · [Database](../backend/schema/README.md) · [Frontend](../frontend/README.md) · [Runtime data](../docs/runtime-data/README.md)

This is the single update procedure for the reported Linux installation. The operator runs the commands. Examples preserve the working paths and use a separate extracted release at `~/Desktop/PAK-Project`.

## Update the working installation

Release **2.0.0-rc.13, delivery revision 2** uses API **8**, core schema **12** and optional OEE **1**. Apply only the owner-run migrations required by the inspected schema. Deploy the backend and supplied frontend build from the same archive. Reuse the working database, private configuration, database roles and any installed OEE v1 module.

### What to install from rc12

| Item | Action |
| --- | --- |
| Backend | Transfer the complete `backend/` from this release. Reuse the working Python virtual environment and run its dependency check. |
| Frontend | Publish the supplied `frontend/dist/` after the upgraded backend passes health. Node and a frontend build are optional when using the supplied assets. |
| Database | Inspect the actual core marker. Core 11 runs 11-to-12, core 10 runs 10-to-11 and then 11-to-12, and valid core 12 skips SQL. |
| Private files | Preserve the working database settings, PM originals, backups and logs under `PAK-data/`. |
| OEE and Nginx | Reuse a valid installed OEE v1 and the existing working proxy. |

Compared with rc12, rc13 adds Assigned Work and CM, the combined and category activity reports, personnel activity by hall, the review interface for PM responsibility and the redesigned management workspace. From rc13 revision 1 with valid core 12, revision 2 needs the new backend and frontend files only.

### 1. Verify and preserve

Verify the complete extracted release before editing it:

```sh
cd "$HOME/Desktop/PAK-Project"
python3 -B tools/verify_source.py
```

Expect `successful: true`, `applicationVersion: 2.0.0-rc.13` and `handoffRevision: 2`. Keep the current code and UI available. Preserve `~/Desktop/PAK-data/backend.env`, seed files and backups. Record the current health response and inspect the database marker using the [database guide](../backend/schema/README.md#1-inspect-the-current-installation).

If the old backend uses `~/Desktop/backend/.env` directly, put a protected copy in the private runtime folder and set `PAK_ENV_FILE` to that copy. Keep the contents of the working settings. See the [settings check](../backend/README.md#2-select-the-private-configuration).

### 2. Stop the intended backend

Stop the foreground process with Ctrl+C, or inspect and stop the recorded detached PID using the [backend runbook](../backend/README.md#5-run-detached-and-inspect-logs). Keep PostgreSQL and Nginx running. Stop other application writers during the maintenance window.

### 3. Back up and upgrade the database

Complete the [backup and restore drill](../backend/schema/README.md#2-back-up-the-current-database) while all writers are stopped. Follow the [migration steps](../backend/schema/README.md#3-apply-only-the-required-migrations) for the inspected marker. Core 10 needs 10-to-11 followed by 11-to-12. Core 11 needs only 11-to-12. A valid core 12 can proceed to verification.

### 4. Replace one complete backend folder

After verification and shutdown, stage a complete backend copy before switching the active folder. Use the same Linux terminal for both blocks.

```sh
PAK_UPDATE_STAMP=$(date +%Y%m%d-%H%M%S)
PAK_NEW_BACKEND="$HOME/Desktop/backend-stage-$PAK_UPDATE_STAMP"
PAK_OLD_BACKEND="$HOME/Desktop/backend-before-$PAK_UPDATE_STAMP"
test ! -e "$PAK_NEW_BACKEND" && test ! -e "$PAK_OLD_BACKEND" &&
  cp -a "$HOME/Desktop/PAK-Project/backend" "$PAK_NEW_BACKEND" &&
  test -f "$PAK_NEW_BACKEND/src/pak_maintenance/__init__.py"
```

Continue only if that check succeeds:

```sh
mv "$HOME/Desktop/backend" "$PAK_OLD_BACKEND" &&
  mv "$PAK_NEW_BACKEND" "$HOME/Desktop/backend"
```

The old source is preserved, while the active source is one complete release. The private runtime directory is not inside either source folder. If your working configuration was still `backend/.env`, select and preserve that actual file before this replacement; do not assume the new folder contains it.

### 5. Check and start

Follow the [backend runbook](../backend/README.md) with the existing environment and private configuration: local preflight, foreground startup, direct health, then detached run if desired. The preflight now shows the selected configuration source and names of environment overrides.

Expect HTTP 200 JSON from backend `2.0.0rc13`, package revision `2`, API `8`, schema `12` and capabilities `personnel-activity-drilldown` and `pm-routing-review`. Once this passes, publish the supplied frontend build using the section below.

### 6. Accept the update

From a client computer, check:

```sh
curl --fail --show-error http://192.168.12.92/api/health
```

1. Sign in with an existing account. Open a historical project and download a PM original.
2. Open report registration and confirm Assigned Work accepts a hall and CM asks for equipment.
3. In Reports, choose a category and open one hall. In personnel performance as the chief, open one person and then one hall. Compare the period and participation minutes with a completed report.
4. As a supervisor, open an eligible pending report and verify every required teammate grade enables the decision control. Use the test installation for a trial submission.
5. Open the PM routing review and inspect the current responsible users before making any transfers. Open an existing OEE sheet if the module is installed.

If the update fails, keep writers stopped and preserve the error and current data. There is no automatic schema downgrade. An older backend requires its matching database schema. Rehearse rollback into an isolated target from the verified pre-upgrade backup and switch only after review. Later data reconciliation needs an explicit plan.

## Install on a new host from a verified backup

Use a separate Linux host or test environment with Python 3.12 or later, a PostgreSQL 17 or newer instance, and the Nginx layout below. Use a verified PAK database backup and the matching private PM originals as your starting data.

1. Extract and verify the release as in step 1. Install the complete backend into the new host's active `backend/` folder.
2. Have the database owner restore into a new database. Follow the isolated-restore commands in the [database guide](../backend/schema/README.md#2-back-up-the-current-database). Use the new target name throughout and compare the restored row counts with the backup inspection.
3. Point the new installation's private settings at the restored database and its limited runtime role. A rehearsed restore with `--no-privileges` requires the [runtime grants](../backend/schema/README.md#runtime-database-role) before startup. Use the OEE permissions instructions only for databases that already contain that module.
4. Check the restored schema marker, then follow the required versioned migrations. Place the matching PM metadata and originals in the [private runtime folder](../docs/runtime-data/README.md).
5. Create the virtual environment and install the pinned dependencies using the [backend guide](../backend/README.md#1-activate-the-environment). Start Uvicorn, check direct health, publish the supplied frontend, configure the proxy and repeat acceptance.

## Nginx setup or recovery

Reuse the existing working proxy for this update. For a new site or a broken proxy, use the following topology.

| Component | Address or path |
| --- | --- |
| Browser / Nginx | `http://192.168.12.92` |
| Uvicorn, on the same host | `127.0.0.1:5187` |
| Docker-published PostgreSQL | `127.0.0.1:5432` from the Uvicorn host |
| Nginx document root | `/home/pak/Desktop/UI` |

Back up the installed configuration before editing. Use either the complete [Nginx example](nginx/nginx.conf) or add the full [API location example](nginx/api-location.example.conf) inside the existing frontend `server {}`. Do not add duplicate `http`/`server` wrappers.

Required behavior:

- `proxy_pass http://127.0.0.1:5187;` retains `/api/...`; no trailing URI slash.
- `^~ /api/` prevents asset regular expressions from intercepting API downloads.
- API errors remain API responses; `proxy_intercept_errors off` and no server-wide SPA error rewrite.
- SPA fallback belongs in frontend `location /` only.
- Ordinary API upload limit is 1 MiB; `/api/parts/import/` uses 8 MiB.
- `index.html`, service worker and manifest are refreshed; hashed assets may be cached.
- PM originals, private settings and database backups remain outside the public document root.

Validate as the account authorized to manage Nginx:

```sh
sudo nginx -t
```

Only after a successful syntax test, reload with the host's existing process-management method, for example:

```sh
sudo systemctl reload nginx
```

Do not start a second Nginx instance.

## Publish a changed frontend

The release includes a matching prebuilt UI. Publish it after backend health and schema checks pass. If editing the frontend, first rebuild and verify it using the [frontend guide](../frontend/README.md). Back up the existing UI, then copy the built contents:

```sh
cp -a "$HOME/Desktop/UI" "$HOME/Desktop/UI-before-$(date +%Y%m%d-%H%M%S)"
cp -a "$HOME/Desktop/PAK-Project/frontend/dist/." "$HOME/Desktop/UI/"
```

Nginx expects `UI/index.html`, not `UI/dist/index.html`. Keep the backup during acceptance. Old hashed assets may remain temporarily for existing browser tabs.

## Diagnose the route

```sh
curl -i -H 'Host: 192.168.12.92' http://127.0.0.1:5187/api/health
curl -i http://192.168.12.92/api/health
```

| Observation | First place to investigate |
| --- | --- |
| Direct request fails | Backend startup and `~/Desktop/PAK-data/logs/uvicorn.log` |
| Direct request works, public request is 502 | Nginx upstream and error log |
| Public health returns HTML | API locations and SPA fallback |
| Health is correct, browser uses an older client | Published build, document root and browser service worker/cache |
| Schema/role validation fails | The database guide and selected configuration; do not initialize a new database |
