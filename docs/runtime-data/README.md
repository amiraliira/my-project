# Runtime data

[Start](../../README.md) · [Backend](../../backend/README.md) · [Database and backups](../../backend/schema/README.md)

Runtime data stays outside source and the public UI. Source replacement must preserve these locations:

```text
~/Desktop/PAK-data/
  backend.env                 private active configuration
  seed/
    pm-import.json            original PM import metadata
    pm-originals/             original documents, named by SHA-256 and extension
  backups/                    native database backups
  logs/
    uvicorn.log
    uvicorn.pid
```

| Data | Authority and preservation |
| --- | --- |
| Users, password hashes, reports, parts, PM plans/revisions and OEE sheets | Existing PostgreSQL database; preserve and verify a current native backup |
| Connection settings and selected data paths | The existing private configuration; keep its actual values |
| Original PM metadata and DOCX/XLSX documents | The existing seed folder; keep exact filenames and bytes |
| Generated website | Nginx `UI/`; back it up for coordinated rollback, but it contains no authoritative business records |

## Configure the seed path

In the selected private file:

```dotenv
PAK_PM_SEED_DIR=~/Desktop/PAK-data/seed
PAK_BACKUP_DIR=~/Desktop/PAK-data/backups
```

Only these two settings expand leading `~/`, using the Uvicorn account's home. The seed setting points to the parent containing both required items, not to `pm-originals/` alone. Preserve another working path if that is where the real files live.

Check:

```sh
test -r "$HOME/Desktop/PAK-data/seed/pm-import.json"
test -d "$HOME/Desktop/PAK-data/seed/pm-originals"
find "$HOME/Desktop/PAK-data/seed/pm-originals" -maxdepth 1 -type f | wc -l
```

An empty `seed/` directory does not satisfy this requirement. The original transferred dataset contained 269 documents; use the preserved installation's actual metadata when checking subsequent additions.

## Recover missing documents

First locate the existing files:

```sh
find "$HOME" -type f -name 'pm-import.json' -print 2>/dev/null
find "$HOME" -type d -name 'pm-originals' -print 2>/dev/null
```

If they exist elsewhere, select that complete parent folder in the private configuration or copy the existing metadata and originals together. If they are absent, recover them from the preserved original-data backup. A separately supplied original-PM archive can restore original documents; it is not a current database backup.

The previously supplied `PAK-PM-Ready.zip` contains `PAK-data/seed/...`. If that archive is on the Linux Desktop, extracting without overwriting existing files uses:

```sh
unzip -n "$HOME/Desktop/PAK-PM-Ready.zip" -d "$HOME/Desktop"
```

The required final directory is `~/Desktop/PAK-data/seed`, not `PAK-data/PAK-data/seed`. Never rename hash-based originals to form titles or edit their bytes; authenticated downloads verify their SHA-256. Old Windows paths inside metadata record provenance and do not need to exist on Linux.

Do not stage/import PM again merely to repair a missing filesystem path. Current template edits, issued work, assignments and password hashes remain in PostgreSQL. Copying original files does not apply a database import or change those records.

## Preserve the full installation

Keep a verified database dump, active private configuration, complete PM seed, any required TLS CA files, and the matching source/UI release identifiers. A database dump alone cannot recover missing original documents. Keep these private items outside GitHub and outside `/home/pak/Desktop/UI`.

Preserve the approved values of `PAK_SCHEDULING_ENABLED` and backup settings. Pausing issuance does not erase plans, and a source update does not authorize reactivating them.
