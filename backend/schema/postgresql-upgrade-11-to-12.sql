-- Explicit owner-run core schema 11 -> 12: add CM and GENERAL as real work types.
-- Stop ALL writers and verify a current native backup/isolated restore first.
-- Existing rows, passwords, PM archives, routing evidence and OEE data are preserved.
-- Runtime defaults to limited role pak; set pak.runtime_user for another role.
-- Run once with psql -X -v ON_ERROR_STOP=1. This one DO statement is atomic.
DO $pak_upgrade$
DECLARE
 runtime_user TEXT := COALESCE(NULLIF(current_setting('pak.runtime_user',true),''),'pak');
 versions INTEGER[];
 source_version INTEGER;
 item RECORD;
 required_column TEXT;
 core_table_name TEXT;
 required_guards JSONB;
 type_constraint RECORD;
 type_count INTEGER;
 type_definition TEXT;
 type_literals TEXT[];
BEGIN
 PERFORM set_config('search_path','public,pg_catalog',true);
 PERFORM pg_advisory_xact_lock(7421989);
 IF runtime_user !~ '^[A-Za-z_][A-Za-z0-9_]{0,62}$' OR runtime_user=current_user THEN
  RAISE EXCEPTION 'a separate explicit simple runtime role is required';
 END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_roles r WHERE r.rolname=runtime_user
  AND NOT(r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication OR r.rolbypassrls))
  OR has_schema_privilege(runtime_user,'public','CREATE') THEN
  RAISE EXCEPTION 'runtime role must exist and have no administrative or schema creation authority';
 END IF;
 IF EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
  WHERE n.nspname='public' AND c.relkind IN ('r','p') AND pg_has_role(runtime_user,c.relowner,'MEMBER')) THEN
  RAISE EXCEPTION 'runtime role must not own or inherit ownership of application tables';
 END IF;
 LOCK TABLE public.approvals,public.assets,public.audit,public.completed_scores,public.legacy_records,public.order_parts,public.orders,public.part_consumption_totals,public.part_consumptions,public.participant_head_scores,public.participant_scores,public.participant_grading_policies,public.pm_responsibility_transfers,public.participants,public.parts,public.plans,public.pm_change_requests,public.pm_occurrences,public.pm_plan_revisions,public.pm_setup_requests,public.pm_submission_archive,public.pm_template_revisions,public.pm_templates,public.pm_weekly_assets,public.pm_weekly_occurrences,public.pm_weekly_plans,public.request_claims,public.schema_version,public.sessions,public.stock_movements,public.stock_request_claims,public.users IN ACCESS EXCLUSIVE MODE;
 SELECT array_agg(version) INTO versions FROM schema_version;
 IF versions IS DISTINCT FROM ARRAY[11] AND versions IS DISTINCT FROM ARRAY[12] THEN
  RAISE EXCEPTION 'exactly one schema-11 or already-applied schema-12 marker is required';
 END IF;
 source_version := versions[1];
 FOREACH core_table_name IN ARRAY ARRAY['approvals','assets','audit','completed_scores','legacy_records','order_parts','orders','part_consumption_totals','part_consumptions','participant_head_scores','participant_scores','participant_grading_policies','pm_responsibility_transfers','participants','parts','plans','pm_change_requests','pm_occurrences','pm_plan_revisions','pm_setup_requests','pm_submission_archive','pm_template_revisions','pm_templates','pm_weekly_assets','pm_weekly_occurrences','pm_weekly_plans','request_claims','schema_version','sessions','stock_movements','stock_request_claims','users'] LOOP
  IF NOT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname='public' AND c.relname=core_table_name AND c.relkind IN ('r','p')
   AND pg_has_role(current_user,c.relowner,'MEMBER')) THEN
   RAISE EXCEPTION 'maintenance connection must own every core table: %',core_table_name;
  END IF;
 END LOOP;
 -- Complete frozen schema-9 storage contract. Minimal startup columns alone
 -- cannot authorize publishing a new marker on incomplete historical data.
 FOR item IN SELECT key AS name,value AS spec FROM jsonb_each($pak_storage_contract${"approvals":{"columns":{"actor_id":"text","coefficient":"numeric","created_at":"text","decision":"text","id":"text","order_id":"text","reason":"text","revision":"integer","specialty":"text","stage":"text"},"keys":["id"]},"assets":{"columns":{"body":"text","code":"text","hall":"text","id":"text","line":"text","name":"text","version":"integer"},"keys":["id"]},"audit":{"columns":{"action":"text","actor_id":"text","actor_name":"text","created_at":"text","details":"text","entity":"text","entity_id":"text","id":"text"},"keys":["id"]},"completed_scores":{"columns":{"head_coefficient":"numeric","head_coefficient_exact":"text","order_id":"text","person_id":"text","revision":"integer","score_cents":"bigint","score_raw":"text","source":"text","supervisor_coefficient":"numeric"},"keys":["order_id","revision","person_id"]},"legacy_records":{"columns":{"body":"text","id":"text","imported_at":"text"},"keys":["id"]},"order_parts":{"columns":{"order_id":"text","part_id":"text","quantity":"numeric","quantity_units":"bigint"},"keys":["order_id","part_id"]},"orders":{"columns":{"asset_id":"text","assignee_id":"text","body":"text","created_at":"text","created_by":"text","description":"text","due_date":"text","duration_minutes":"integer","id":"text","number":"text","status":"text","title":"text","type":"text","updated_at":"text","version":"integer","work_date":"text","workflow_revision":"integer"},"keys":["id"]},"part_consumption_totals":{"columns":{"part_id":"text","quantity_units":"bigint","report_count":"bigint"},"keys":["part_id"]},"part_consumptions":{"columns":{"actor_id":"text","actor_name":"text","approval_id":"text","approved_at":"text","executed_at":"text","id":"text","order_id":"text","order_number":"text","part_code":"text","part_id":"text","part_name":"text","quantity_units":"bigint","revision":"integer","unit":"text"},"keys":["id"]},"participant_head_scores":{"columns":{"actor_id":"text","approval_id":"text","coefficient":"text","created_at":"text","order_id":"text","person_id":"text","revision":"integer","specialty":"text"},"keys":["order_id","revision","person_id"]},"participant_scores":{"columns":{"actor_id":"text","approval_id":"text","coefficient":"numeric","created_at":"text","order_id":"text","person_id":"text","revision":"integer","specialty":"text"},"keys":["order_id","revision","person_id"]},"participants":{"columns":{"minutes":"integer","order_id":"text","person_id":"text","specialty":"text"},"keys":["order_id","person_id"]},"parts":{"columns":{"code":"text","id":"text","name":"text","specification":"text","unit":"text","unit_cost":"numeric","version":"integer"},"keys":["id"]},"plans":{"columns":{"active":"integer","asset_id":"text","body":"text","id":"text","interval_days":"integer","next_due":"text","version":"integer"},"keys":["id"]},"pm_change_requests":{"columns":{"base_version":"integer","created_at":"text","decided_at":"text","decision_reason":"text","entity_id":"text","id":"text","operation":"text","payload":"text","payload_hash":"text","request_key":"text","requester_id":"text","result":"text","reviewer_id":"text","status":"text","version":"integer"},"keys":["id"]},"pm_occurrences":{"columns":{"due_date":"text","order_id":"text","plan_id":"text"},"keys":["plan_id","due_date"]},"pm_plan_revisions":{"columns":{"actor_id":"text","body":"text","created_at":"text","plan_id":"text","reason":"text","revision":"integer"},"keys":["plan_id","revision"]},"pm_setup_requests":{"columns":{"actor_id":"text","body":"text","created_at":"text","payload_hash":"text","request_key":"text"},"keys":["actor_id","request_key"]},"pm_submission_archive":{"columns":{"asset_code":"text","asset_id":"text","asset_name":"text","body":"text","body_sha256":"text","evidence_source":"text","executed_at":"text","hall":"text","id":"text","number":"text","order_id":"text","performer_id":"text","performer_name":"text","plan_id":"text","plan_version":"integer","specialty":"text","submitted_at":"text","submitted_by":"text","supervisor_id":"text","template_id":"text","template_version":"integer","title":"text","work_date":"text","workflow_revision":"integer"},"keys":["id"]},"pm_template_revisions":{"columns":{"actor_id":"text","body":"text","created_at":"text","reason":"text","revision":"integer","template_id":"text"},"keys":["template_id","revision"]},"pm_templates":{"columns":{"body":"text","hall":"text","id":"text","mapping_status":"text","period":"text","specialty":"text","title":"text","version":"integer"},"keys":["id"]},"pm_weekly_assets":{"columns":{"asset_id":"text","order_id":"text"},"keys":["order_id","asset_id"]},"pm_weekly_occurrences":{"columns":{"end_utc":"text","executed_at":"text","month":"integer","order_id":"text","persian_year":"integer","plan_id":"text","plan_version":"integer","specialty":"text","start_utc":"text","submitted_at":"text","supervisor_id":"text","template_id":"text","template_version":"integer","week":"integer"},"keys":["plan_id","persian_year","month","week"]},"pm_weekly_plans":{"columns":{"active":"integer","asset_id":"text","body":"text","cursor_month":"integer","hall":"text","id":"text","mapping_confirmed":"integer","next_scan":"text","specialty":"text","start_date":"text","supervisor_id":"text","template_id":"text","version":"integer"},"keys":["id"]},"request_claims":{"columns":{"actor_id":"text","order_id":"text","payload_hash":"text","request_key":"text"},"keys":["actor_id","request_key"]},"schema_version":{"columns":{"version":"integer"},"keys":[]},"sessions":{"columns":{"epoch":"integer","expires":"text","id":"text","user_id":"text"},"keys":["id"]},"stock_movements":{"columns":{"actor_id":"text","created_at":"text","id":"text","kind":"text","note":"text","part_id":"text","quantity":"numeric","quantity_units":"bigint","reference":"text"},"keys":["id"]},"stock_request_claims":{"columns":{"actor_id":"text","movement_id":"text","payload_hash":"text","request_key":"text"},"keys":["actor_id","request_key"]},"users":{"columns":{"active":"integer","code":"text","id":"text","name":"text","password_hash":"text","role":"text","session_epoch":"integer","specialty":"text"},"keys":["id"]},"participant_grading_policies":{"columns":{"order_id":"text","revision":"integer","person_id":"text","specialty":"text","role_at_submission":"text","policy":"text","created_at":"text"},"keys":["order_id","revision","person_id"]},"pm_responsibility_transfers":{"columns":{"id":"text","order_id":"text","order_version":"integer","from_supervisor_id":"text","to_supervisor_id":"text","actor_id":"text","reason":"text","created_at":"text"},"keys":["id"]}}$pak_storage_contract$::jsonb) LOOP
  IF (SELECT jsonb_object_agg(column_name,data_type) FROM information_schema.columns WHERE table_schema='public' AND table_name=item.name) IS DISTINCT FROM item.spec->'columns' THEN
   RAISE EXCEPTION 'complete core column/type contract differs: %',item.name;
  END IF;
  IF COALESCE((SELECT jsonb_agg(k.column_name ORDER BY k.ordinal_position) FROM information_schema.table_constraints t JOIN information_schema.key_column_usage k ON k.constraint_name=t.constraint_name AND k.constraint_schema=t.constraint_schema AND k.table_name=t.table_name WHERE t.table_schema='public' AND t.table_name=item.name AND t.constraint_type='PRIMARY KEY'),'[]'::jsonb) IS DISTINCT FROM item.spec->'keys' THEN
   RAISE EXCEPTION 'complete core primary-key contract differs: %',item.name;
  END IF;
 END LOOP;
 IF (SELECT COUNT(DISTINCT p.proname) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.proname IN ('pak_round_even','pak_decimal','pak_participant_score','pak_decimal_sum'))<>4 THEN
  RAISE EXCEPTION 'exact PostgreSQL scoring functions are required';
 END IF;
 IF (SELECT COUNT(*) FROM pg_constraint WHERE connamespace='public'::regnamespace AND convalidated AND ((conname='ck_order_parts_units' AND conrelid='public.order_parts'::regclass) OR (conname='ck_movements_units' AND conrelid='public.stock_movements'::regclass)))<>2 THEN
  RAISE EXCEPTION 'validated quantity constraints are required';
 END IF;
 IF EXISTS(SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_type<>'BASE TABLE')
  OR EXISTS(SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name NOT IN ('assets','legacy_records','parts','pm_templates','schema_version','users','audit','orders','part_consumption_totals','plans','pm_setup_requests','pm_template_revisions','pm_weekly_plans','sessions','stock_movements','approvals','order_parts','participants','pm_occurrences','pm_plan_revisions','pm_weekly_assets','request_claims','stock_request_claims','part_consumptions','participant_scores','participant_head_scores','completed_scores','pm_weekly_occurrences','pm_change_requests','pm_submission_archive','participant_grading_policies','pm_responsibility_transfers','oee_module','oee_lines','oee_sheets','oee_sheet_revisions')) THEN
  RAISE EXCEPTION 'unknown core schema objects are not accepted';
 END IF;
 IF EXISTS(SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name IN ('oee_module','oee_lines','oee_sheets','oee_sheet_revisions')) THEN
  IF (SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public' AND table_name IN ('oee_module','oee_lines','oee_sheets','oee_sheet_revisions'))<>4 THEN
   RAISE EXCEPTION 'optional OEE module is incomplete';
  END IF;
  IF (SELECT array_agg(version) FROM public.oee_module) IS DISTINCT FROM ARRAY[1] THEN
   RAISE EXCEPTION 'optional OEE module marker is not version 1';
  END IF;
  IF (SELECT COUNT(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND NOT t.tgisinternal AND t.tgenabled IN ('O','A') AND t.tgname IN ('oee_revisions_immutable','oee_sheets_no_delete'))<>2 THEN
   RAISE EXCEPTION 'optional OEE history guards are missing';
  END IF;
 END IF;
 FOR item IN SELECT key AS name,value AS columns FROM jsonb_each($pak_contract${"approvals": ["actor_id", "coefficient", "decision", "id", "order_id", "revision", "stage"], "assets": ["body", "code", "hall", "id", "line", "name", "version"], "audit": ["action", "actor_id", "actor_name", "created_at", "details", "entity", "entity_id", "id"], "completed_scores": ["head_coefficient", "head_coefficient_exact", "order_id", "person_id", "revision", "score_cents", "score_raw", "source", "supervisor_coefficient"], "legacy_records": ["body", "id", "imported_at"], "order_parts": ["order_id", "part_id", "quantity_units"], "orders": ["body", "id", "status", "version", "work_date", "workflow_revision"], "part_consumption_totals": ["part_id", "quantity_units", "report_count"], "part_consumptions": ["approval_id", "approved_at", "id", "order_id", "part_id", "quantity_units"], "participant_grading_policies": ["created_at", "order_id", "person_id", "policy", "revision", "role_at_submission", "specialty"], "participant_head_scores": ["actor_id", "approval_id", "coefficient", "created_at", "order_id", "person_id", "revision", "specialty"], "participant_scores": ["approval_id", "coefficient", "order_id", "person_id", "revision"], "participants": ["minutes", "order_id", "person_id", "specialty"], "parts": ["code", "id", "unit", "unit_cost", "version"], "plans": ["active", "asset_id", "body", "id", "interval_days", "next_due", "version"], "pm_change_requests": ["base_version", "created_at", "decided_at", "decision_reason", "entity_id", "id", "operation", "payload", "payload_hash", "request_key", "requester_id", "result", "reviewer_id", "status", "version"], "pm_occurrences": ["due_date", "order_id", "plan_id"], "pm_plan_revisions": ["actor_id", "body", "created_at", "plan_id", "revision"], "pm_responsibility_transfers": ["actor_id", "created_at", "from_supervisor_id", "id", "order_id", "order_version", "reason", "to_supervisor_id"], "pm_setup_requests": ["actor_id", "body", "created_at", "payload_hash", "request_key"], "pm_submission_archive": ["asset_code", "asset_id", "asset_name", "body", "body_sha256", "evidence_source", "executed_at", "hall", "id", "number", "order_id", "performer_id", "performer_name", "plan_id", "plan_version", "specialty", "submitted_at", "submitted_by", "supervisor_id", "template_id", "template_version", "title", "work_date", "workflow_revision"], "pm_template_revisions": ["actor_id", "body", "created_at", "revision", "template_id"], "pm_templates": ["body", "id", "mapping_status", "period", "version"], "pm_weekly_assets": ["asset_id", "order_id"], "pm_weekly_occurrences": ["month", "order_id", "persian_year", "plan_id", "plan_version", "template_version", "week"], "pm_weekly_plans": ["active", "id", "mapping_confirmed", "start_date", "template_id", "version"], "request_claims": ["actor_id", "order_id", "payload_hash", "request_key"], "sessions": ["epoch", "expires", "id", "user_id"], "stock_movements": ["created_at", "id", "part_id", "quantity_units"], "stock_request_claims": ["actor_id", "movement_id", "payload_hash", "request_key"], "users": ["active", "code", "id", "password_hash", "role", "session_epoch", "specialty"]}$pak_contract$::jsonb) LOOP
  FOR required_column IN SELECT jsonb_array_elements_text(item.columns) LOOP
   IF NOT EXISTS(SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public'
    AND c.table_name=item.name AND c.column_name=required_column) THEN
    RAISE EXCEPTION 'required core column is missing: %.%',item.name,required_column;
   END IF;
  END LOOP;
  IF NOT has_table_privilege(runtime_user,'public.' || item.name,'SELECT')
   OR NOT has_table_privilege(runtime_user,'public.' || item.name,'INSERT')
   OR NOT has_table_privilege(runtime_user,'public.' || item.name,'UPDATE')
   OR NOT has_table_privilege(runtime_user,'public.' || item.name,'DELETE')
   OR has_table_privilege(runtime_user,'public.' || item.name,'TRUNCATE')
   OR has_table_privilege(runtime_user,'public.' || item.name,'TRIGGER') THEN
   RAISE EXCEPTION 'runtime role needs existing CRUD only, without TRUNCATE/TRIGGER, on %',item.name;
  END IF;
 END LOOP;
 required_guards := $pak_new_guards$["guard_approval_evidence_insert", "guard_approvals_history", "guard_audit_history", "guard_completed_score_evidence_insert", "guard_completed_scores_history", "guard_consumption_evidence_insert", "guard_consumption_history", "guard_consumption_total_delete", "guard_consumption_total_update", "guard_participant_grading_policies_history", "guard_participant_grading_policy_insert", "guard_participant_head_score_evidence_insert", "guard_participant_head_scores_history", "guard_participant_score_evidence_insert", "guard_participant_scores_history", "guard_pm_archive_correction_insert", "guard_pm_archive_history", "guard_pm_archive_insert", "guard_pm_change_request_history", "guard_pm_responsibility_transfer_insert", "guard_pm_responsibility_transfers_history", "guard_request_claims_history", "pak_pm_members_immutable", "pak_pm_occurrence_identity_guard", "pak_pm_occurrence_no_delete", "pak_pm_plan_revision_immutable", "pak_pm_setup_immutable", "pak_pm_template_revision_immutable"]$pak_new_guards$::jsonb;
 FOR required_column IN SELECT jsonb_array_elements_text(required_guards) LOOP
  IF NOT EXISTS(SELECT 1 FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname='public' AND t.tgname=required_column AND NOT t.tgisinternal AND t.tgenabled IN ('O','A')) THEN
   RAISE EXCEPTION 'reviewed enabled evidence guard missing: %',required_column;
  END IF;
 END LOOP;
 IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='public.pm_submission_archive'::regclass AND conname='pm_submission_archive_evidence_source_check' AND convalidated AND pg_get_constraintdef(oid) LIKE '%supervisor-correction-snapshot%') THEN
  RAISE EXCEPTION 'schema-10 correction archive constraint is absent or unvalidated';
 END IF;
 FOR item IN SELECT key AS name,value AS hash FROM jsonb_each_text($pak_guard_hashes${"pak_guard_pm_archive_correction_insert": "db21457f60ce7357b6cff5403ed37f94ead71413173ef6f735cce2a1eba35bef", "pak_guard_pm_archive_insert": "9daf0812195d98ad7543c9c88a6e9d0f7169485d81182c225c63e9d25edde354", "pak_guard_pm_responsibility_transfer": "521510db3330384059911f895403edbb77bef476727569251a76bfe91b160b76"}$pak_guard_hashes$::jsonb) LOOP
  IF NOT EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
   WHERE n.nspname='public' AND p.proname=item.name AND p.pronargs=0 AND p.prorettype='trigger'::regtype
    AND encode(sha256(convert_to(replace(p.prosrc,E'\r\n',E'\n'),'UTF8')),'hex')=item.hash) THEN
   RAISE EXCEPTION 'schema-11 reviewed routing guard differs: %',item.name;
  END IF;
 END LOOP;
 IF NOT EXISTS(SELECT 1 FROM pg_index i JOIN pg_class idx ON idx.oid=i.indexrelid JOIN pg_namespace ns ON ns.oid=idx.relnamespace WHERE ns.nspname='public' AND idx.relname='ix_pm_weekly_plan_owner_template' AND i.indrelid='public.pm_weekly_plans'::regclass AND i.indisvalid AND i.indisready AND NOT i.indisunique AND i.indpred IS NULL AND i.indexprs IS NULL AND ARRAY(SELECT a.attname::text FROM unnest(i.indkey::smallint[]) WITH ORDINALITY k(attnum,ord) JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.attnum ORDER BY k.ord)=ARRAY['supervisor_id','template_id'])
 OR NOT EXISTS(SELECT 1 FROM pg_index i JOIN pg_class idx ON idx.oid=i.indexrelid JOIN pg_namespace ns ON ns.oid=idx.relnamespace WHERE ns.nspname='public' AND idx.relname='ix_pm_weekly_template_order' AND i.indrelid='public.pm_weekly_occurrences'::regclass AND i.indisvalid AND i.indisready AND NOT i.indisunique AND i.indpred IS NULL AND i.indexprs IS NULL AND ARRAY(SELECT a.attname::text FROM unnest(i.indkey::smallint[]) WITH ORDINALITY k(attnum,ord) JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.attnum ORDER BY k.ord)=ARRAY['template_id','order_id']) THEN
  RAISE EXCEPTION 'schema-11 routing lookup indexes missing or invalid';
 END IF;
 SELECT COUNT(*) INTO type_count FROM pg_constraint
 WHERE conrelid='public.orders'::regclass AND contype='c'
  AND conkey=ARRAY[(SELECT attnum FROM pg_attribute WHERE attrelid='public.orders'::regclass AND attname='type')]::smallint[];
 IF type_count<>1 THEN
  RAISE EXCEPTION 'exactly one reviewed work-type constraint is required';
 END IF;
 SELECT conname,convalidated,pg_get_constraintdef(oid) AS definition INTO type_constraint
 FROM pg_constraint WHERE conrelid='public.orders'::regclass AND contype='c'
  AND conkey=ARRAY[(SELECT attnum FROM pg_attribute WHERE attrelid='public.orders'::regclass AND attname='type')]::smallint[];
 type_definition:=lower(regexp_replace(replace(type_constraint.definition,'::text',''),'\s','','g'));
 type_literals:=ARRAY(SELECT parts[1] FROM regexp_matches(type_constraint.definition, $pak_literal$'([^']*)'$pak_literal$, 'g') AS parts);
 IF NOT type_constraint.convalidated THEN
  RAISE EXCEPTION 'the work-type constraint must be validated';
 END IF;
 IF source_version=12 THEN
  IF type_constraint.conname<>'ck_orders_work_type' OR type_definition<>$pak_types$check((type=any(array['pm','em','cm','project','general'])))$pak_types$
   OR type_literals<>ARRAY['PM','EM','CM','PROJECT','GENERAL'] THEN
   RAISE EXCEPTION 'schema-12 work-type constraint differs from the reviewed contract';
  END IF;
  RAISE NOTICE 'schema 12 is already applied; verified with no changes';
  RETURN;
 END IF;
 IF type_definition<>$pak_old_types$check((type=any(array['pm','em','project'])))$pak_old_types$
  OR type_literals<>ARRAY['PM','EM','PROJECT'] THEN
  RAISE EXCEPTION 'schema-11 work-type constraint differs from the reviewed source';
 END IF;
 EXECUTE format('ALTER TABLE public.orders DROP CONSTRAINT %I',type_constraint.conname);
 ALTER TABLE public.orders ADD CONSTRAINT ck_orders_work_type CHECK(type IN ('PM','EM','CM','PROJECT','GENERAL'));
 UPDATE schema_version SET version=12 WHERE version=11;
 IF (SELECT array_agg(version) FROM schema_version) IS DISTINCT FROM ARRAY[12] THEN
  RAISE EXCEPTION 'schema-12 marker was not published atomically';
 END IF;
END;
$pak_upgrade$;
