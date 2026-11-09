# Detached startup and logs

Run from the `backend` folder with your Python virtual environment active and your private `.env` configured.

## Start detached

```sh
nohup uvicorn app.main:app --host 127.0.0.1 --port 5187 --workers 1 --no-proxy-headers > uvicorn.log 2>&1 < /dev/null &
```

The backend continues after the terminal disconnects. Standard output and error are written to `uvicorn.log` in the directory where you start it. This command does not install a service or restart the backend after a crash or reboot.

A background job/PID only confirms that the command was launched. Check for `Application startup complete` in the log; configuration or database errors can still make startup fail.

## Follow the log

```sh
tail -f uvicorn.log
```

Press Ctrl+C to stop the log viewer. This does not stop the detached backend.

Read recent lines without following:

```sh
tail -n 100 uvicorn.log
```

## Append to an existing log

The original command uses `>` and replaces the previous log on each start. To append instead, use this alternative command:

```sh
nohup uvicorn app.main:app --host 127.0.0.1 --port 5187 --workers 1 --no-proxy-headers >> uvicorn.log 2>&1 < /dev/null &
```

Choose one startup command. Do not launch a second instance while the first is using port 5187.

## Check through Nginx

After startup completes and API forwarding is active:

```sh
curl --fail http://192.168.12.92/api/health
```

Use the configured public address through Nginx. The private upstream expects the selected proxy headers. Share relevant errors rather than your private `.env` or passwords.

See [manual startup](manual-uvicorn.md) and [PM paths](pm-files.md). Logging here uses shell redirection; no new `.env` logging key is needed.
