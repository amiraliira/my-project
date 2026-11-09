# PM files in the rc.12 update

The update ZIP does not include replacement seed or company data. Keep your current external `PAK-data/seed/` directory. The older rc.10 data package described below is the historical baseline, not an update to live PM assignments or completed work.

# PM files: what belongs on disk and what belongs in PostgreSQL

The backend needs both the existing PostgreSQL database and the original PM form files. A database restore alone does not install the original documents.

## Required disk layout

Set `PAK_PM_SEED_DIR` to the **seed folder itself**, not to its parent and not to `pm-originals`:

```text
~/Desktop/PAK-data/seed/                 <-- PAK_PM_SEED_DIR
├── pm-import.json
└── pm-originals/
    ├── <64-character-sha256>.docx
    ├── <64-character-sha256>.xlsx
    └── ...
```

Use your own Desktop so the data folders can be prepared without `sudo`:

```sh
mkdir -p "$HOME/Desktop/PAK-data/seed" "$HOME/Desktop/PAK-data/backups"
```

Then put these values in the backend's private `.env`:

```dotenv
PAK_PM_SEED_DIR=~/Desktop/PAK-data/seed
PAK_BACKUP_DIR=~/Desktop/PAK-data/backups
```

Leading `~/` expands to the home of the user running Uvicorn. It does not refer to the Nginx worker user automatically, and it changes if you run Uvicorn as another account. Use your normal intended account rather than `sudo`. These two settings also accept absolute local paths. Other `.env` values remain literal; `$HOME` expansion and shell execution are not performed.

The `mkdir` command creates empty folders only. You must still copy `pm-import.json` **and the complete `pm-originals/` directory** into the seed folder. Do not put these files in Nginx's public frontend root.

The delivered `PAK-Data-2.0.0-rc.10.zip` contains:

```text
PAK-Data-rc10/
├── acceptance-data.json
├── data/acceptance.dump
└── seed/
    ├── pm-import.json
    └── pm-originals/
```

Copy the contents of its `seed/` folder into your chosen runtime seed folder. The original bundle contains **269 source documents: 255 DOCX and 14 XLSX**. `acceptance-data.json` lists the seed files with their sizes and SHA256 hashes. The supplied dump is a reset acceptance baseline, not a backup of current company operations; this guide does not instruct you to restore it over an existing database.

## The three kinds of PM information

| Information | Location | Purpose |
| --- | --- | --- |
| Import metadata and parsed layouts | `PAK_PM_SEED_DIR/pm-import.json` | Source identities, original file hashes, parsed form tables, proposed schedule bindings and unresolved mapping notes |
| Original source documents | `PAK_PM_SEED_DIR/pm-originals/` | Unmodified DOCX/XLSX evidence and downloads of the original forms |
| Working forms, plans, issued work and submitted answers | PostgreSQL | Current templates/plans, their revisions, assignments, work reports and immutable submission evidence |

Do not treat `pm-import.json` as a live database. Editing it does not edit an existing template, change an issued sheet or update a schedule. Existing database revisions may be newer than the preserved seed input.

## Why the original filenames look like hashes

Each template's `source` metadata records the original path and its `sha256`. The backend keeps the original `.docx` or `.xlsx` extension and looks for:

```text
PAK_PM_SEED_DIR/pm-originals/<source.sha256><original-extension>
```

For example, a source hash of 64 hexadecimal characters and an original `.docx` path resolves to that hash followed by `.docx`. The historical Desktop path in `source.path` is provenance; the Linux server does not need that old Desktop directory.

`PmService._original()` in `backend/src/pak_maintenance/pm_service.py` checks the hash format, permitted extension, file location and SHA256 of the actual bytes before returning a download. Renaming documents to human titles, changing their bytes or pointing `PAK_PM_SEED_DIR` at the wrong folder breaks that lookup. A hash mismatch is reported as an altered original; a missing document is reported as a missing original.

## How forms reach the user

The application serves original forms through authenticated API routes. Nginx forwards those `/api` requests to the backend. Do **not** add an unauthenticated Nginx `alias` or public static folder for `pm-originals`.

Relevant routes include:

```text
GET /api/pm/templates/{id}/original
GET /api/pm/tasks/{id}/original
GET /api/pm/archive/{id}/original
```

Template/task access follows the current user's workflow permissions. The dedicated PM archive is available only to the active `planner` role, including its original-document download. Being a database administrator or application `admin` does not grant that archive permission.

When a PM sheet is issued, it refers to a specific template and plan revision. Submitted results and execution details are kept in the submission archive while the original layout remains in the referenced template revision. Editing a later template must not replace the layout used by earlier issued work. An archived original download therefore resolves the source metadata of the archived template revision.

## Import and activation are separate from startup

Normal Uvicorn startup does not import forms, restore a database, migrate schema, create application accounts or activate PM schedules.

The explicit PM staging action checks every original file before inserting missing templates and plans. New staged plans are inactive and unconfirmed. Do not run staging again merely to fix a path when the database already contains the templates.

`PAK_SCHEDULING_ENABLED=false` is the default for the new manual entry point. The existing database's plan activation state is separate. Keep scheduling paused until the approved start decision; an old start date is not a new activation instruction.

## Permissions and backups

The operating-system account running Uvicorn needs read access to the seed directory and original forms. It needs write access to the configured backup directory when backups are enabled. Keep the seed files outside the source release so replacing code does not replace form evidence.

A complete recovery plan includes the PostgreSQL backup, `pm-import.json`, all original documents, and the protected local configuration/TLS material. Keep secrets separate from a source ZIP. If you move the seed folder, update the configured setting and verify original-document downloads for both current and archived forms.

## Source map

- `pm_service.py`: staging, PM tasks, original-file verification and original downloads.
- `pm_registry.py`: equipment/form registry and coverage views.
- `pm_setup.py`: explicit activation decisions.
- `pm_calendar.py` and `pm_schedule.py`: Persian calendar windows and schedule rules.
- `pm_template_lifecycle.py` and `pm_governance.py`: template/plan changes, review and revision preservation.
- `pm_archive.py`: submitted answers and execution evidence; planner-only retrieval.
- `backend/schema/`: database tables, constraints and immutable-evidence guards.