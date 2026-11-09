"""Explicit additive OEE extension; application startup never invokes DDL."""
from pathlib import Path
import re


def enable_oee(database, *, runtime_user):
    if database.config.provider!='postgresql' or not isinstance(runtime_user,str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,62}',runtime_user):
        raise ValueError('PostgreSQL and an explicit limited runtime role are required')
    if runtime_user==database.config.user:
        raise ValueError('Schema owner and application account must be separate')
    database.verify_schema(runtime_role=False)
    script=(Path(__file__).resolve().parents[2]/'schema/postgresql-oee-v1.sql').read_text(encoding='utf-8')
    def operation(c):
        c.execute('SELECT pg_advisory_xact_lock(158137761,1)')
        role=c.one('SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=:role',dict(role=runtime_user))
        if not role or any(role.values()):
            raise ValueError('OEE requires an existing limited runtime account')
        present=bool(c.scalar("SELECT to_regclass('public.oee_module') IS NOT NULL"))
        if not present:
            c.raw.execute(script)
        if c.query('SELECT version FROM oee_module') != [dict(version=1)]:
            raise ValueError('OEE module marker is not version 1')
        tables={r['table_name'] for r in c.query("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name IN('oee_module','oee_lines','oee_sheets','oee_sheet_revisions')")}
        if tables != {'oee_module','oee_lines','oee_sheets','oee_sheet_revisions'}:
            raise ValueError('OEE module is incomplete; no repair was applied')
        guards={r['tgname'] for r in c.query("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal AND tgenabled IN('O','A') AND tgname IN('oee_revisions_immutable','oee_sheets_no_delete')")}
        if guards != {'oee_revisions_immutable','oee_sheets_no_delete'}:
            raise ValueError('OEE immutable evidence guards are missing')
        c.execute(f'GRANT SELECT ON oee_module TO "{runtime_user}"')
        c.execute(f'GRANT SELECT,INSERT,UPDATE ON oee_lines,oee_sheets TO "{runtime_user}"')
        c.execute(f'GRANT SELECT,INSERT ON oee_sheet_revisions TO "{runtime_user}"')
        return dict(module='oee',version=1,coreSchemaVersion=10,alreadyApplied=present,existingDataReset=False)
    return database.run_transaction(operation)
