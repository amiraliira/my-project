-- Read-only inventory for the existing application database. No passwords shown.
-- Run through psql with -X -v ON_ERROR_STOP=1; do not use initialization SQL.
\pset pager off
BEGIN READ ONLY;
SELECT current_database() AS database_name, current_user AS inspection_user,
       current_setting('server_version') AS postgresql_version;
SELECT version AS core_schema_version FROM public.schema_version;
SELECT table_name AS optional_oee_table
FROM information_schema.tables
WHERE table_schema='public'
  AND table_name IN ('oee_module','oee_lines','oee_sheets','oee_sheet_revisions')
ORDER BY table_name;
SELECT to_regclass('public.oee_module') IS NOT NULL AS oee_present \gset
\if :oee_present
SELECT version AS optional_oee_version FROM public.oee_module;
\else
\echo Optional OEE is not installed.
\endif
SELECT rolname, rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls,
       has_schema_privilege(rolname,'public','CREATE') AS public_schema_create
FROM pg_roles WHERE rolname='pak';
SELECT c.relname AS table_name, pg_get_userbyid(c.relowner) AS table_owner
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='public' AND c.relkind IN ('r','p') ORDER BY c.relname;
SELECT 'users' AS table_name, count(*) AS row_count FROM public.users
UNION ALL SELECT 'orders',count(*) FROM public.orders
UNION ALL SELECT 'participants',count(*) FROM public.participants
UNION ALL SELECT 'parts',count(*) FROM public.parts
UNION ALL SELECT 'part_consumptions',count(*) FROM public.part_consumptions
UNION ALL SELECT 'stock_movements',count(*) FROM public.stock_movements
UNION ALL SELECT 'pm_templates',count(*) FROM public.pm_templates
UNION ALL SELECT 'pm_weekly_plans',count(*) FROM public.pm_weekly_plans
UNION ALL SELECT 'pm_submission_archive',count(*) FROM public.pm_submission_archive
UNION ALL SELECT 'audit',count(*) FROM public.audit
ORDER BY table_name;
-- Aggregate checksum only: no password or individual password hash is emitted.
-- Keep the inspection output private. Compare before/after while all writers stop.
SELECT count(*) AS account_count,
       md5(string_agg(id || ':' || password_hash,E'\n' ORDER BY id)) AS password_hash_fingerprint
FROM public.users;
COMMIT;
