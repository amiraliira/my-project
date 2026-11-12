# Runtime data and PM files

[Project checklist](../../README.md) · [Backend](../../backend/README.md) · [Database and backups](../../backend/schema/README.md) · [OEE](../../backend/oee/README.md)

Code, current business records and original PM documents are different things. Replacing source files must not replace runtime data.

## 1. Know what to preserve

| Item | Current location | Preserve during an update |
| --- | --- | --- |
| Reports, users, password hashes, assignments, templates, revisions and stock records | Existing PostgreSQL database | Yes; migrate this database after a verified backup |
| Private backend configuration | Active `.env` or the path selected by `PAK_ENV_FILE` | Yes; follow the backend README to preserve and select one private file |
| PM import metadata | Configured seed folder, `pm-import.json` | Yes |
| Original PM documents | Configured seed folder, `pm-originals/` | Yes; retain exact bytes and filenames |
| Database snapshots | Operator backup destination | Yes; store outside source and public UI |
| Built UI | Nginx document root | Back up for coordinated rollback; contains no authoritative business data |
| Source release | Extracted source folder | Keep the prior release for reference and rollback planning |

An old baseline dump is not a backup of the current server. Restoring it over the live database loses subsequent records and password changes. This update does not require importing baseline data.

## 2. Use user-owned external folders

Run as the same Linux user that runs Uvicorn:

```sh
mkdir -p "$HOME/Desktop/PAK-data/seed" "$HOME/Desktop/PAK-data/backups"
```

Creating these directories does not copy data into them. Preserve the actual existing files, even if their current path differs from this example. The required seed layout is:

```text
~/Desktop/PAK-data/seed/
├── pm-import.json
└── pm-originals/
    ├── <sha256>.docx
    ├── <sha256>.xlsx
    └── ...
```

In the private `.env` of the backend you will run:

```dotenv
PAK_PM_SEED_DIR=~/Desktop/PAK-data/seed
PAK_BACKUP_DIR=~/Desktop/PAK-data/backups
```

Only these two runtime directory settings expand leading `~/` to the Uvicorn user's home. Do not put `$HOME` into `.env` expecting shell expansion. Running Uvicorn as another user changes what `~/` means. Use an absolute path when needed.

The seed folder itself is `PAK_PM_SEED_DIR`: not its parent and not `pm-originals/`. The operating-system user running Uvicorn must read it; that user must write the backup directory. These Desktop paths need no `sudo` when owned by that user.

## 3. Check the files before restarting

```sh
test -r "$HOME/Desktop/PAK-data/seed/pm-import.json"
test -d "$HOME/Desktop/PAK-data/seed/pm-originals"
find "$HOME/Desktop/PAK-data/seed/pm-originals" -maxdepth 1 -type f | wc -l
```

Check against the files you actually preserved; an empty directory is insufficient. The historical baseline contained 269 original documents, but current installation contents must be verified from its own preserved data and source metadata rather than inferred from that old count.

Original document names use the recorded SHA-256 plus the original extension. Do not rename them to form titles or edit their bytes: authenticated original downloads verify the stored hash. The historical Windows path stored in source metadata is provenance; Linux does not need that Windows directory.

## 4. Do not confuse seed and working records

`pm-import.json` contains original import metadata and parsed input layouts. It is not a live database. Editing it does not update an existing template or issued sheet. Current plans, edits, assignments, responses, evidence and password hashes are held in PostgreSQL and may be newer than the seed.

Normal Uvicorn startup does not restore a database, recreate application accounts, import PM forms, run schema migrations or activate plans. Do not re-run PM staging to repair a missing path. Correct the path and verify downloads instead.

`PAK_SCHEDULING_ENABLED=false` pauses automatic issuance; it does not erase plans or their activation state. Keep that value until the explicit PM start decision.

## 5. Back up the complete installation

Keep together, with restricted access:

1. A verified native backup of the current PostgreSQL database; see the [database README](../../backend/schema/README.md).
2. The actual seed folder with both metadata and all originals.
3. The active private `.env` and any database TLS CA files needed for reconnection.
4. The matching code release and frontend build identifier.

Store private copies outside GitHub and outside `/home/pak/Desktop/UI`. Original PM downloads must remain behind authenticated `/api` routes; do not create a public Nginx `alias` for the seed folder.

After an update, test both an existing PM sheet and an archived original download with an account allowed to access them. A database-only backup cannot recover a missing original DOCX/XLSX file.
