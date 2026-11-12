# Nginx and frontend publishing

[Project checklist](../README.md) · [Backend](../backend/README.md) · [Frontend build](../frontend/README.md) · [Database](../backend/schema/README.md)

This is a manual deployment guide for the existing HTTP site. The operator applies configuration and runs commands. The source package does not log in to or configure the company server.

## 1. Confirm the three addresses

| Component | Address / path | Meaning |
| --- | --- | --- |
| Browser / Nginx | `http://192.168.12.92` | Public website origin on the company LAN |
| Uvicorn | `127.0.0.1:5187` | Backend listener on the same server as Nginx |
| PostgreSQL | `127.0.0.1:5432` | Docker-published database port, as seen from that server |
| Frontend document root | `/home/pak/Desktop/UI` | Built public files only |

The public origin belongs in the private backend `.env` as `PAK_PUBLIC_URL=http://192.168.12.92`. The current HTTP exception is `PAK_ALLOW_INSECURE_HTTP=true`. Keep `PAK_DEVELOPMENT=false`. See [backend configuration](../backend/README.md).

## 2. Start and verify the backend first

Complete the [database](../backend/schema/README.md) and [backend](../backend/README.md) steps. On the Linux server:

```sh
curl -i -H 'Host: 192.168.12.92' http://127.0.0.1:5187/api/health
```

Expected: HTTP 200 and JSON. A refused connection means Uvicorn is not listening. Read the backend error before changing Nginx or the database. The `__version__` import error happens before database startup and is covered in the [backend troubleshooting section](../backend/README.md).

## 3. Update the existing Nginx server block

Two reference files are provided:

- [`nginx/api-location.example.conf`](nginx/api-location.example.conf): add these API locations **inside the existing `server {}`** that serves the frontend.
- [`nginx/nginx.conf`](nginx/nginx.conf): a complete example based on the supplied configuration. Review the paths, worker account and listeners before replacing an installed configuration.

Use one approach; do not paste a second `http {}` into an existing `server {}` or add duplicate locations. Back up the installed configuration before editing it.

The API configuration must retain these properties:

```nginx
location ^~ /api/ {
    # Keep the complete headers, limits and timeouts from the supplied example.
    proxy_pass http://127.0.0.1:5187;
}
```

The snippet above explains routing only; copy the complete provided example. `proxy_pass` has **no URI or trailing slash**, so the backend receives `/api/...`. `^~` prevents the asset regular expression from intercepting API downloads such as a PDF. The provided example also handles exact `/api` and the bounded XLSX import route.

- Keep `proxy_intercept_errors off` for API requests.
- Do not convert API 401/403/404/500 responses to `/index.html`.
- Use the SPA fallback only in frontend `location /`.
- Ordinary API bodies are bounded at 1 MiB; `/api/parts/import/` is bounded at 8 MiB.
- Run Uvicorn with `--no-proxy-headers`, as in the backend README. These examples assume one local Nginx hop.
- Do not expose the seed directory, database backups or `backend/.env` through `root` or `alias`.

The supplied full example removes the old server-wide `error_page 404 /index.html`; `try_files` provides the frontend SPA fallback without rewriting backend errors.

## 4. Publish the matching built frontend

Assumptions: the release is `$HOME/Desktop/PAK-rc12`, the operator account is `pak`, and the verified build exists in its `frontend/dist/`. Stop application writers for the coordinated update; do not mix an rc12 client with the old rc10 backend.

First preserve the current public folder and inspect the new build:

```sh
cd "$HOME/Desktop/PAK-rc12"
test -s frontend/dist/index.html
test -d frontend/dist/assets
cp -a /home/pak/Desktop/UI "/home/pak/Desktop/UI-before-$(date +%Y%m%d-%H%M%S)"
```

Run the copy only after all preceding checks succeed:

```sh
cp -a frontend/dist/. /home/pak/Desktop/UI/
```

This copies the **contents** of `dist`, including static files. Do not copy `dist` as a nested directory; Nginx expects `/home/pak/Desktop/UI/index.html`. Old hashed asset files may remain temporarily to support already-open tabs. Do not delete the old UI backup during acceptance.

If the intended worker account cannot read the Desktop path, fix access for that account or choose an accessible public document root. Avoid blanket `chmod -R 777` and never grant public access to the entire Desktop.

## 5. Validate and reload Nginx

Use an account authorized to manage Nginx. On a systemd installation:

```sh
sudo nginx -t
```

Only after the syntax test succeeds:

```sh
sudo systemctl reload nginx
```

If this host does not manage Nginx through systemd, use its existing process-management method. Do not start a second Nginx instance. These administrative commands are separate from normal Uvicorn, PM files and backup paths, which do not need `sudo`.

## 6. Verify the whole route

```sh
curl -i -H 'Host: 192.168.12.92' http://127.0.0.1:5187/api/health
curl -i http://192.168.12.92/api/health
curl -I http://192.168.12.92/index.html
```

Both health requests should return HTTP 200 JSON describing the matching backend. `index.html` should be served without a long-lived cache. `sw.js` and the manifest also need the no-cache locations in the provided configuration; hashed assets may be cached for one year.

| Result | Next action |
| --- | --- |
| Loopback health fails | Fix the backend first; inspect `uvicorn.log` |
| Loopback works, public health is 502 | Check Nginx upstream/listener and Nginx error log |
| Public health returns HTML | Fix API location precedence and SPA error handling |
| Public JSON is correct, browser shows rc10 | Check frontend root, published build and browser cache |
| Backend reports schema mismatch | Follow the database README; do not initialize a replacement database |

Keep maintenance active until login, an existing report, an original PM download and the required application workflows pass. OEE activation is a separate [optional database step](../backend/oee/README.md).
