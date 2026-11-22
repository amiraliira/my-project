"""Schema-11 routing guard fingerprints; no startup DDL or automatic repair."""
import hashlib

POSTGRESQL_GUARD_HASHES = {'pak_guard_pm_responsibility_transfer': '521510db3330384059911f895403edbb77bef476727569251a76bfe91b160b76', 'pak_guard_pm_archive_insert': '9daf0812195d98ad7543c9c88a6e9d0f7169485d81182c225c63e9d25edde354', 'pak_guard_pm_archive_correction_insert': 'db21457f60ce7357b6cff5403ed37f94ead71413173ef6f735cce2a1eba35bef'}
SQLITE_GUARD_HASHES = {'guard_pm_responsibility_transfer_insert': '469c4a00b6331c5e7464e585132069e4b7f2bc0d9268f97802bcfac5b963f6f3', 'guard_pm_archive_insert': '6db61a5472887d963a412663b8edebf190738fd2b6c0d9e9c71e5ad5d5df7d9a', 'guard_pm_archive_correction_insert': '313bac68f077bd746c8c2559d8c845ba378e478ff57f7bd9d3b37f48b1832415'}
ROUTING_INDEXES = {
    'ix_pm_weekly_plan_owner_template': ('pm_weekly_plans', ('supervisor_id','template_id')),
    'ix_pm_weekly_template_order': ('pm_weekly_occurrences', ('template_id','order_id')),
}

def _digest(text):
    return hashlib.sha256(text.replace('\r\n','\n').encode()).hexdigest()

def verify_routing_contract(c):
    from .database import SchemaMismatchError
    if c.is_sqlite:
        rows=c.query("SELECT name,sql FROM sqlite_master WHERE type='trigger' AND name IN ('guard_pm_responsibility_transfer_insert','guard_pm_archive_insert','guard_pm_archive_correction_insert')")
        actual={row['name']:_digest(' '.join(row['sql'].strip().rstrip(';').split())) for row in rows}
        expected=SQLITE_GUARD_HASHES
        for name,(table,columns) in ROUTING_INDEXES.items():
            index=c.one("SELECT tbl_name FROM sqlite_master WHERE type='index' AND name=:name",{'name':name})
            if not index or index['tbl_name']!=table or tuple(row['name'] for row in c.query('PRAGMA index_info('+name+')'))!=columns:
                raise SchemaMismatchError('Schema 11 requires the reviewed PM responsibility lookup indexes')
    else:
        rows=c.query("SELECT p.proname AS name,p.prosrc AS body FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.pronargs=0 AND p.prorettype='trigger'::regtype AND p.proname IN ('pak_guard_pm_responsibility_transfer','pak_guard_pm_archive_insert','pak_guard_pm_archive_correction_insert')")
        actual={row['name']:_digest(row['body']) for row in rows}
        expected=POSTGRESQL_GUARD_HASHES
        for name,(table,columns) in ROUTING_INDEXES.items():
            index=c.one("SELECT tab.relname AS table_name,i.indisvalid,i.indisready,i.indpred IS NULL AS not_partial,ARRAY(SELECT a.attname FROM unnest(i.indkey::smallint[]) WITH ORDINALITY k(attnum,ord) JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.attnum ORDER BY k.ord) AS columns FROM pg_index i JOIN pg_class idx ON idx.oid=i.indexrelid JOIN pg_class tab ON tab.oid=i.indrelid JOIN pg_namespace n ON n.oid=tab.relnamespace WHERE n.nspname='public' AND idx.relname=:name",{'name':name})
            if not index or index['table_name']!=table or not all(index[key] for key in ('indisvalid','indisready','not_partial')) or tuple(index['columns'])!=columns:
                raise SchemaMismatchError('Schema 11 requires the reviewed PM responsibility lookup indexes')
    if actual!=expected:
        raise SchemaMismatchError('Schema 11 PM routing guards differ from the reviewed execution-team contract. Apply the explicit owner upgrade; startup did not modify anything.')
