# Run FastAPI manually behind your existing Nginx

For the rc.12 update of an existing installation, follow [the Persian update guide](update-rc12-fa.md) first; preserve the existing database, private settings and application accounts. The remaining sections explain the manual runtime layout.

This package supplies a normal `app.main:app` entry point. You run Uvicorn yourself. It does not configure the server, install Nginx, create services or restore your database.

## 1. The addresses are different for each connection

```text
Client browser
    http://192.168.12.92/api/...
             |
             v
Existing Nginx: frontend files + /api forwarding
             |
             v
Uvicorn: http://127.0.0.1:5187
             |
             v
PostgreSQL: 127.0.0.1:<your actual published PostgreSQL port>
```

For this same-host arrangement, use **`127.0.0.1` as the database host**, not `192.168.12.92` and not the literal hostname `localhost`. The application deliberately validates numeric internal addresses. If PostgreSQL is in Docker, its port must be published on this same host and match `PAK_DB_PORT`; a container-only port is not automatically reachable on the host loopback address.

The frontend calls `/api/...` on the same Nginx address used to load the page. Do not put `localhost` in browser API URLs: there it would mean the client's own computer. No frontend API URL change or CORS setup is needed for the same-origin `/api` arrangement.

## 2. Put the backend and runtime files in clear locations

For example:

```text
~/Desktop/PAK-Update-rc12-r2/backend/                  source release; run Uvicorn here
    app/main.py
    app/settings.py
    src/pak_maintenance/
    schema/
    requirements.txt
    .env                          your private local settings
~/Desktop/PAK-data/seed/              PM files; see pm-files.md
    pm-import.json
    pm-originals/
~/Desktop/PAK-data/backups/           writable backup destination
```

These paths are under your own Desktop, so creating the backend/data folders does not require `sudo`. Keep your existing Nginx frontend directory `/home/pak/Desktop/UI` unchanged. PM originals belong in the separate data folder, not in the public frontend root.

Create the two data directories as the same Linux user who will run Uvicorn:

```sh
mkdir -p "$HOME/Desktop/PAK-data/seed" "$HOME/Desktop/PAK-data/backups"
```

Creating an empty directory is not enough: extract the supplied PM data package and copy **both `pm-import.json` and the complete `pm-originals/` folder** into `~/Desktop/PAK-data/seed`. Keep the originals' hash-based filenames. See [PM files](pm-files.md) for the exact tree.

Set these two values in your private `.env`:

```dotenv
PAK_PM_SEED_DIR=~/Desktop/PAK-data/seed
PAK_BACKUP_DIR=~/Desktop/PAK-data/backups
```

The settings loader expands leading `~/` only for these two runtime directory settings. It uses the home directory of the user **running Uvicorn**, not the Nginx `user pak` setting. Run Uvicorn as your normal intended account, without `sudo`; running it under another account changes what `~` means. An explicit absolute path is also supported.

The `.env` parser does not expand `$HOME`, passwords or other values. Use `~/...` or an absolute path for these two settings; use `$HOME` only in the shell command above. Nginx configuration management remains your separate server task.

## 3. Prepare your Python environment and settings

From your Desktop backend folder on the Linux server:

```sh
cd "$HOME/Desktop/PAK-Update-rc12-r2/backend"
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip check
cp -n .env.example .env
chmod 600 .env
nano .env
```

Use Python 3.12 or newer; the recorded baseline used Python 3.14.8. Install the pinned packages from your approved package source. If you install offline, replace the install line with your prepared Linux wheelhouse command:

```sh
python -m pip install --no-index --find-links /your/approved/linux-wheelhouse -r requirements.txt
```

No Visual Studio, `uv`, `fastapi dev`, or `fastapi[standard]` command is required for this startup path. `requirements.txt` is the reviewed pin list. Do not overwrite it with `pip freeze > requirements.txt`.

The new entry point automatically reads `backend/.env`. To keep it elsewhere, set `PAK_ENV_FILE` to that existing file's absolute path before starting Uvicorn. Process environment variables override values in the file. The file is read as bounded, literal settings; it is **not executed as a shell script**. Do not run `source .env`.

The copy command preserves an existing `.env`. Edit that local file rather than replacing it during later updates. The parser preserves punctuation in password values and does not support shell expansion or inline comments.

Fill these settings:

| Setting | Value for your arrangement |
| --- | --- |
| `PAK_PUBLIC_URL` | `http://192.168.12.92` for your current port-80 site |
| `PAK_ALLOW_INSECURE_HTTP` | `true` for the HTTP-only setup you confirmed; switch to `false` when using HTTPS |
| `PAK_DEVELOPMENT` | Keep `false`; HTTP is enabled by its separate explicit setting |
| `PAK_DB_HOST` | `127.0.0.1` on the database server; `192.168.12.92` only for the temporary other-machine test below |
| `PAK_DB_PORT` | Your actual PostgreSQL host port; normally `5432` |
| `PAK_DB_ALLOW_LAN_PLAINTEXT` | Default `false`; explicit temporary private-LAN database test only, as below |
| `PAK_DB_NAME` | The existing application's database name; it cannot be inferred from the server IP or container name |
| `PAK_DB_USER` | Your PostgreSQL runtime login, for example the database login you called `admin` |
| `PAK_DB_PASSWORD` | Enter its password only in the protected local file or process environment |
| `PAK_PM_SEED_DIR` | `~/Desktop/PAK-data/seed`; it must contain `pm-import.json` and `pm-originals` |
| `PAK_BACKUP_DIR` | `~/Desktop/PAK-data/backups`, writable by the Uvicorn user |
| `PAK_SCHEDULING_ENABLED` | Keep `false` until the approved PM start decision |
| `PAK_AUTOMATIC_BACKUP_ENABLED` | Default `false`; enable only when real dump/restore executables are configured |

For an explicitly approved local database connection without PostgreSQL TLS, the same-host example uses:

```dotenv
PAK_DB_SSLMODE=disable
PAK_DB_ALLOW_LOOPBACK_PLAINTEXT=true
```

`PAK_DB_ALLOW_LOOPBACK_PLAINTEXT` only permits a loopback database address. It is separate from the browser HTTP setting and from the explicit private-LAN test setting below. To use database TLS, set `PAK_DB_SSLMODE=verify-full`, provide `PAK_DB_SSLROOTCERT` with the local CA file and use a matching database certificate.

### Temporary backend-on-another-machine test

When Uvicorn is running on your other computer and PostgreSQL remains on the server, `127.0.0.1` would point to the wrong computer. For this temporary test, set the following in that computer's private backend `.env`:

```dotenv
PAK_DB_HOST=192.168.12.92
PAK_DB_PORT=5432
PAK_DB_SSLMODE=disable
PAK_DB_ALLOW_LAN_PLAINTEXT=true
PAK_DB_ALLOW_LOOPBACK_PLAINTEXT=false
PAK_DB_SSLROOTCERT=
```

Keep `PAK_DB_NAME`, `PAK_DB_USER` and `PAK_DB_PASSWORD` set to the actual existing database and its credentials. Use `pak_maintenance` as the database name only if that is the database you already created; the host IP does not determine its name. The blank `PAK_DB_SSLROOTCERT` line means no CA is supplied for this plaintext test; remove or comment an old CA setting rather than combining it with `sslmode=disable`.

`PAK_DB_ALLOW_LAN_PLAINTEXT` defaults to `false`; setting it to `true` explicitly permits an **unencrypted private-LAN database test**. It does not permit a public-IP database target and does not enable development mode or relax schema/runtime-role checks. Restart Uvicorn after changing `.env`.

For database TLS, set `PAK_DB_SSLMODE=verify-full`, `PAK_DB_ALLOW_LAN_PLAINTEXT=false`, and restore the correct `PAK_DB_SSLROOTCERT` path and matching server certificate. When the backend is moved onto the database server, return `PAK_DB_HOST` to `127.0.0.1` and use the same-host database settings above.

This changes the **backend-to-database connection only**. The existing Nginx upstream `127.0.0.1:5187` is still correct for the final setup with Uvicorn deployed on the Nginx server. Do not edit that upstream for this database-connection test. A Uvicorn process started on the other computer is not reached by the server's loopback upstream; Nginx routing and direct browser access are separate from whether the database connection succeeds.

A database login called `admin` is not the application's admin account and is not necessarily suitable as its runtime role. Startup still rejects a superuser, table owner/member of an owner role, schema-creation privileges and other excessive privileges. It also checks the required table permissions. The application does not silently grant permissions or change your database.

Your current Nginx site is HTTP only, as you explicitly confirmed. Set:

```dotenv
PAK_PUBLIC_URL=http://192.168.12.92
PAK_ALLOW_INSECURE_HTTP=true
PAK_DEVELOPMENT=false
```

`PAK_ALLOW_INSECURE_HTTP` is a separate explicit opt-in; its default is `false`. It permits the current HTTP browser/API connection without enabling development mode or relaxing database role/schema checks. This connection, including login, is plain HTTP on your internal network. When you enable HTTPS in Nginx, set `PAK_PUBLIC_URL=https://192.168.12.92` and `PAK_ALLOW_INSECURE_HTTP=false`, then restart Uvicorn. Keep the upstream Uvicorn bind on `127.0.0.1`; Nginx can terminate HTTPS later.

## 4. Start Uvicorn yourself

Keep the virtual environment active and stay in `backend`:

```sh
uvicorn app.main:app --host 127.0.0.1 --port 5187 --workers 1 --no-proxy-headers
```

The module exposes `app`, so Uvicorn can import it normally. The small `app` layer loads the local settings and constructs the existing FastAPI/domain application. You do not need to edit `api.py` or add an `app = FastAPI()` to that internal module. The manual module supports normal ASGI server import syntax as described in the [FastAPI manual-server documentation](https://fastapi.tiangolo.com/deployment/manually/).

Use one worker while the application runs its PM scheduler and backup worker. Do not use `--reload` for the server deployment. You control the process lifetime; this package does not install a service. Stop your manual process with Ctrl+C when you need to stop it.

`--no-proxy-headers` is intentional. The application's explicit one-hop Nginx adapter checks the real loopback peer and the expected forwarded headers before restoring the configured public scheme and client address. Uvicorn must not replace the peer address first. Do not expose this upstream port on `0.0.0.0`.

### Detached startup and logs

```sh
nohup uvicorn app.main:app --host 127.0.0.1 --port 5187 --workers 1 --no-proxy-headers > uvicorn.log 2>&1 < /dev/null &
```

View the log:

```sh
tail -f uvicorn.log
```

Ctrl+C stops the log viewer, not the background backend. The `>` replaces the previous log; use `>>` to append. See [logging details](logs.md).

## 5. Add only API forwarding to the existing Nginx server block

The complete updated configuration is `deploy/nginx/nginx.conf`, based on the configuration you supplied. It preserves your frontend root `/home/pak/Desktop/UI`, HTTP port 80 and existing frontend route/cache settings. It adds the API locations and lists `192.168.12.92 localhost` as server names. `deploy/nginx/api-location.example.conf` is also available if you prefer to merge only the API locations yourself.

The essential directives are:

```nginx
location ^~ /api/ {
    proxy_pass http://127.0.0.1:5187;
    proxy_set_header Host $http_host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_cache off;
    proxy_intercept_errors off;
}
```

Keep **no trailing slash** after `5187` in `proxy_pass`. The backend routes already begin with `/api`; this form preserves the request URI. Nginx documents the distinction between passing an upstream URL with and without a URI in its [proxy_pass reference](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_pass).

Nginx must overwrite `X-Forwarded-For` with `$remote_addr`; do not append an incoming client-supplied chain with `$proxy_add_x_forwarded_for` in this single-hop setup. The public Host and scheme must match `PAK_PUBLIC_URL`. Existing cookies and CSRF headers pass through to the API normally.

After you have applied the fragment to your own site and checked the configuration, test through the public Nginx origin:

```sh
curl --fail http://192.168.12.92/api/health
```

Use the configured public origin, including any nondefault port. When you later enable HTTPS, the client must trust its certificate/CA. A raw request to `http://127.0.0.1:5187/api/health` without the trusted-proxy headers is not the normal browser path and may be rejected.

The global `error_page 404 /index.html` fallback was removed from your supplied configuration. Your frontend `try_files $uri $uri/ /index.html` already handles SPA routes; a global 404-to-HTML rule can conceal genuinely missing files. API locations explicitly keep upstream error responses and disable proxy caching, so API errors remain API responses rather than frontend HTML. Exact `/sw.js` and `/manifest.webmanifest` locations now return no-cache headers so they are refreshed rather than caught by the existing one-year JavaScript/asset cache rule. Hashed Vite assets keep your existing one-year caching. The rc.12 update includes a rebuilt frontend; deploy its dist files together with the API-7 backend. API locations also keep the backend `DENY` framing policy and `nosniff` header without conflicting duplicates from the frontend server headers.

You apply and validate the file yourself:

```sh
sudo nginx -t
sudo systemctl reload nginx
```

Run the reload only if the configuration test passes. This package does not write to your server or reload Nginx for you.

## 6. What a successful server start does and does not establish

A successful lifespan startup verifies the existing PostgreSQL schema and runtime permissions. The rc.12 core requires schema 10 and PostgreSQL 17 or newer; the OEE add-on is a separate schema-1 extension. An empty database you have just created still needs the approved application structure/data. No automatic restore, migration, reset, import, account provisioning or PM activation occurs during normal startup.

This update preserves existing application accounts and password hashes. A completely new, empty installation is a separate provisioning task: a healthy `/api/health` response does not prepare its first application login. Do not restore the reset baseline over a live database, substitute the personal test launcher, or use a PostgreSQL password as an application password.

The API is the existing authenticated route contract; the application does not expose public Swagger `/docs` or Redoc endpoints. The same user permissions, revision checks, archive evidence and company CIP calculation remain in the domain code.

If startup fails, retain the error class and traceback locally and share a version with credentials removed. Do not paste the complete private `.env` or a complete database connection string. First distinguish import/configuration errors from database TLS, schema and runtime-role errors; changing the Uvicorn import name cannot fix those later prerequisites.

See [PM files and their paths](pm-files.md) for original-document downloads and recovery requirements.