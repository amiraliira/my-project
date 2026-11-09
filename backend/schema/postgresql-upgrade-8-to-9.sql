-- Explicit owner-run core schema 8 -> 9. Stop ALL writers and verify a native
-- pg_dump backup first. No table drops, seed restore or historical data rewrite.
-- Runtime role defaults to the reviewed deployment role pak. For a different
-- existing limited role, use upgrade_core_schema.py --runtime-user ROLE, or
-- BEGIN; SELECT set_config('pak.runtime_user','ROLE',true); execute this file;
-- COMMIT. Identifiers are validated and quoted, never interpolated untrusted.
-- This DO statement, including DDL/grants/marker, is one atomic transaction.
DO $pak_upgrade$
DECLARE
 runtime_user TEXT := COALESCE(NULLIF(current_setting('pak.runtime_user',true),''),'pak');
 versions INTEGER[];
 source_version INTEGER;
 item RECORD;
 required_column TEXT;
 core_table_name TEXT;
 required_guards JSONB;
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
 LOCK TABLE public.approvals,public.assets,public.audit,public.completed_scores,public.legacy_records,public.order_parts,public.orders,public.part_consumption_totals,public.part_consumptions,public.participant_head_scores,public.participant_scores,public.participants,public.parts,public.plans,public.pm_change_requests,public.pm_occurrences,public.pm_plan_revisions,public.pm_setup_requests,public.pm_submission_archive,public.pm_template_revisions,public.pm_templates,public.pm_weekly_assets,public.pm_weekly_occurrences,public.pm_weekly_plans,public.request_claims,public.schema_version,public.sessions,public.stock_movements,public.stock_request_claims,public.users IN ACCESS EXCLUSIVE MODE;
 SELECT array_agg(version) INTO versions FROM schema_version;
 IF versions IS DISTINCT FROM ARRAY[8] AND versions IS DISTINCT FROM ARRAY[9] THEN
  RAISE EXCEPTION 'exactly one schema-8 or already-applied schema-9 marker is required';
 END IF;
 source_version := versions[1];
 FOREACH core_table_name IN ARRAY ARRAY['approvals','assets','audit','completed_scores','legacy_records','order_parts','orders','part_consumption_totals','part_consumptions','participant_head_scores','participant_scores','participants','parts','plans','pm_change_requests','pm_occurrences','pm_plan_revisions','pm_setup_requests','pm_submission_archive','pm_template_revisions','pm_templates','pm_weekly_assets','pm_weekly_occurrences','pm_weekly_plans','request_claims','schema_version','sessions','stock_movements','stock_request_claims','users'] LOOP
  IF NOT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname='public' AND c.relname=core_table_name AND c.relkind IN ('r','p')
   AND pg_has_role(current_user,c.relowner,'MEMBER')) THEN
   RAISE EXCEPTION 'maintenance connection must own every core table: %',core_table_name;
  END IF;
 END LOOP;
 -- Complete frozen schema-8 storage contract. Minimal startup columns alone
 -- cannot authorize publishing a new marker on incomplete historical data.
 FOR item IN SELECT key AS name,value AS spec FROM jsonb_each($pak_storage_contract${"approvals":{"columns":{"actor_id":"text","coefficient":"numeric","created_at":"text","decision":"text","id":"text","order_id":"text","reason":"text","revision":"integer","specialty":"text","stage":"text"},"keys":["id"]},"assets":{"columns":{"body":"text","code":"text","hall":"text","id":"text","line":"text","name":"text","version":"integer"},"keys":["id"]},"audit":{"columns":{"action":"text","actor_id":"text","actor_name":"text","created_at":"text","details":"text","entity":"text","entity_id":"text","id":"text"},"keys":["id"]},"completed_scores":{"columns":{"head_coefficient":"numeric","head_coefficient_exact":"text","order_id":"text","person_id":"text","revision":"integer","score_cents":"bigint","score_raw":"text","source":"text","supervisor_coefficient":"numeric"},"keys":["order_id","revision","person_id"]},"legacy_records":{"columns":{"body":"text","id":"text","imported_at":"text"},"keys":["id"]},"order_parts":{"columns":{"order_id":"text","part_id":"text","quantity":"numeric","quantity_units":"bigint"},"keys":["order_id","part_id"]},"orders":{"columns":{"asset_id":"text","assignee_id":"text","body":"text","created_at":"text","created_by":"text","description":"text","due_date":"text","duration_minutes":"integer","id":"text","number":"text","status":"text","title":"text","type":"text","updated_at":"text","version":"integer","work_date":"text","workflow_revision":"integer"},"keys":["id"]},"part_consumption_totals":{"columns":{"part_id":"text","quantity_units":"bigint","report_count":"bigint"},"keys":["part_id"]},"part_consumptions":{"columns":{"actor_id":"text","actor_name":"text","approval_id":"text","approved_at":"text","executed_at":"text","id":"text","order_id":"text","order_number":"text","part_code":"text","part_id":"text","part_name":"text","quantity_units":"bigint","revision":"integer","unit":"text"},"keys":["id"]},"participant_head_scores":{"columns":{"actor_id":"text","approval_id":"text","coefficient":"text","created_at":"text","order_id":"text","person_id":"text","revision":"integer","specialty":"text"},"keys":["order_id","revision","person_id"]},"participant_scores":{"columns":{"actor_id":"text","approval_id":"text","coefficient":"numeric","created_at":"text","order_id":"text","person_id":"text","revision":"integer","specialty":"text"},"keys":["order_id","revision","person_id"]},"participants":{"columns":{"minutes":"integer","order_id":"text","person_id":"text","specialty":"text"},"keys":["order_id","person_id"]},"parts":{"columns":{"code":"text","id":"text","name":"text","specification":"text","unit":"text","unit_cost":"numeric","version":"integer"},"keys":["id"]},"plans":{"columns":{"active":"integer","asset_id":"text","body":"text","id":"text","interval_days":"integer","next_due":"text","version":"integer"},"keys":["id"]},"pm_change_requests":{"columns":{"base_version":"integer","created_at":"text","decided_at":"text","decision_reason":"text","entity_id":"text","id":"text","operation":"text","payload":"text","payload_hash":"text","request_key":"text","requester_id":"text","result":"text","reviewer_id":"text","status":"text","version":"integer"},"keys":["id"]},"pm_occurrences":{"columns":{"due_date":"text","order_id":"text","plan_id":"text"},"keys":["plan_id","due_date"]},"pm_plan_revisions":{"columns":{"actor_id":"text","body":"text","created_at":"text","plan_id":"text","reason":"text","revision":"integer"},"keys":["plan_id","revision"]},"pm_setup_requests":{"columns":{"actor_id":"text","body":"text","created_at":"text","payload_hash":"text","request_key":"text"},"keys":["actor_id","request_key"]},"pm_submission_archive":{"columns":{"asset_code":"text","asset_id":"text","asset_name":"text","body":"text","body_sha256":"text","evidence_source":"text","executed_at":"text","hall":"text","id":"text","number":"text","order_id":"text","performer_id":"text","performer_name":"text","plan_id":"text","plan_version":"integer","specialty":"text","submitted_at":"text","submitted_by":"text","supervisor_id":"text","template_id":"text","template_version":"integer","title":"text","work_date":"text","workflow_revision":"integer"},"keys":["id"]},"pm_template_revisions":{"columns":{"actor_id":"text","body":"text","created_at":"text","reason":"text","revision":"integer","template_id":"text"},"keys":["template_id","revision"]},"pm_templates":{"columns":{"body":"text","hall":"text","id":"text","mapping_status":"text","period":"text","specialty":"text","title":"text","version":"integer"},"keys":["id"]},"pm_weekly_assets":{"columns":{"asset_id":"text","order_id":"text"},"keys":["order_id","asset_id"]},"pm_weekly_occurrences":{"columns":{"end_utc":"text","executed_at":"text","month":"integer","order_id":"text","persian_year":"integer","plan_id":"text","plan_version":"integer","specialty":"text","start_utc":"text","submitted_at":"text","supervisor_id":"text","template_id":"text","template_version":"integer","week":"integer"},"keys":["plan_id","persian_year","month","week"]},"pm_weekly_plans":{"columns":{"active":"integer","asset_id":"text","body":"text","cursor_month":"integer","hall":"text","id":"text","mapping_confirmed":"integer","next_scan":"text","specialty":"text","start_date":"text","supervisor_id":"text","template_id":"text","version":"integer"},"keys":["id"]},"request_claims":{"columns":{"actor_id":"text","order_id":"text","payload_hash":"text","request_key":"text"},"keys":["actor_id","request_key"]},"schema_version":{"columns":{"version":"integer"},"keys":[]},"sessions":{"columns":{"epoch":"integer","expires":"text","id":"text","user_id":"text"},"keys":["id"]},"stock_movements":{"columns":{"actor_id":"text","created_at":"text","id":"text","kind":"text","note":"text","part_id":"text","quantity":"numeric","quantity_units":"bigint","reference":"text"},"keys":["id"]},"stock_request_claims":{"columns":{"actor_id":"text","movement_id":"text","payload_hash":"text","request_key":"text"},"keys":["actor_id","request_key"]},"users":{"columns":{"active":"integer","code":"text","id":"text","name":"text","password_hash":"text","role":"text","session_epoch":"integer","specialty":"text"},"keys":["id"]}}$pak_storage_contract$::jsonb) LOOP
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
  IF source_version=8 AND item.name IN ('participant_grading_policies','pm_responsibility_transfers') THEN
   IF to_regclass('public.' || item.name) IS NOT NULL THEN
    RAISE EXCEPTION 'schema-8 contains a partial schema-9 table: %',item.name;
   END IF;
   CONTINUE;
  END IF;
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
 required_guards := CASE WHEN source_version=8 THEN $pak_old_guards$["guard_approval_evidence_insert", "guard_approvals_history", "guard_audit_history", "guard_completed_score_evidence_insert", "guard_completed_scores_history", "guard_consumption_evidence_insert", "guard_consumption_history", "guard_consumption_total_delete", "guard_consumption_total_update", "guard_participant_head_score_evidence_insert", "guard_participant_head_scores_history", "guard_participant_score_evidence_insert", "guard_participant_scores_history", "guard_pm_archive_history", "guard_pm_archive_insert", "guard_pm_change_request_history", "guard_request_claims_history", "pak_pm_members_immutable", "pak_pm_occurrence_identity_guard", "pak_pm_occurrence_no_delete", "pak_pm_plan_revision_immutable", "pak_pm_setup_immutable", "pak_pm_template_revision_immutable"]$pak_old_guards$::jsonb
  ELSE $pak_new_guards$["guard_approval_evidence_insert", "guard_approvals_history", "guard_audit_history", "guard_completed_score_evidence_insert", "guard_completed_scores_history", "guard_consumption_evidence_insert", "guard_consumption_history", "guard_consumption_total_delete", "guard_consumption_total_update", "guard_participant_grading_policies_history", "guard_participant_grading_policy_insert", "guard_participant_head_score_evidence_insert", "guard_participant_head_scores_history", "guard_participant_score_evidence_insert", "guard_participant_scores_history", "guard_pm_archive_history", "guard_pm_archive_insert", "guard_pm_change_request_history", "guard_pm_responsibility_transfer_insert", "guard_pm_responsibility_transfers_history", "guard_request_claims_history", "pak_pm_members_immutable", "pak_pm_occurrence_identity_guard", "pak_pm_occurrence_no_delete", "pak_pm_plan_revision_immutable", "pak_pm_setup_immutable", "pak_pm_template_revision_immutable"]$pak_new_guards$::jsonb END;
 FOR required_column IN SELECT jsonb_array_elements_text(required_guards) LOOP
  IF NOT EXISTS(SELECT 1 FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname='public' AND t.tgname=required_column AND NOT t.tgisinternal AND t.tgenabled IN ('O','A')) THEN
   RAISE EXCEPTION 'reviewed enabled evidence guard missing: %',required_column;
  END IF;
 END LOOP;
 IF source_version=9 THEN
  RAISE NOTICE 'schema 9 is already applied; verified with no changes';
  RETURN;
 END IF;
 EXECUTE $pak_ddl$
-- Explicit owner-run schema 8 -> 9 extension; never run by the application.
-- Historical evidence is retained. Per-policy guards enforce head ranges 3/9.
ALTER TABLE participant_head_scores DROP CONSTRAINT participant_head_scores_coefficient_check;
ALTER TABLE participant_head_scores ADD CONSTRAINT participant_head_scores_coefficient_check
 CHECK(length(coefficient) BETWEEN 1 AND 31 AND CAST(coefficient AS NUMERIC)>=0 AND CAST(coefficient AS NUMERIC)<=9);
CREATE TABLE participant_grading_policies(
 order_id TEXT NOT NULL REFERENCES orders(id),
 revision INTEGER NOT NULL CHECK(revision>0),
 person_id TEXT NOT NULL REFERENCES users(id),
 specialty TEXT NOT NULL,
 role_at_submission TEXT NOT NULL CHECK(role_at_submission IN ('technician','supervisor','planner','head','manager','admin')),
 policy TEXT NOT NULL CHECK(policy IN ('two-stage-v1','head-only-v1')),
 created_at TEXT NOT NULL,
 PRIMARY KEY(order_id,revision,person_id),
 CHECK((role_at_submission='supervisor' AND policy='head-only-v1') OR (role_at_submission<>'supervisor' AND policy='two-stage-v1'))
);

-- Explicit migration owner only. Runtime never changes issued PM identity.
CREATE TABLE IF NOT EXISTS pm_responsibility_transfers(
 id TEXT PRIMARY KEY,
 order_id TEXT NOT NULL REFERENCES pm_weekly_occurrences(order_id),
 order_version INTEGER NOT NULL CHECK(order_version>0),
 from_supervisor_id TEXT NOT NULL REFERENCES users(id),
 to_supervisor_id TEXT NOT NULL REFERENCES users(id),
 actor_id TEXT NOT NULL REFERENCES users(id),
 reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 3 AND 2000),
 created_at TEXT NOT NULL,
 CHECK(from_supervisor_id<>to_supervisor_id),
 UNIQUE(order_id,order_version)
);
CREATE INDEX IF NOT EXISTS ix_pm_responsibility_owner ON pm_responsibility_transfers(to_supervisor_id,order_id,order_version);
CREATE OR REPLACE FUNCTION pak_guard_pm_responsibility_transfer() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 -- Serialize direct SQL inserts with assignment, submission and other transfers.
 PERFORM 1 FROM orders WHERE id=NEW.order_id FOR UPDATE;
 IF NOT EXISTS(SELECT 1 FROM orders o JOIN pm_weekly_occurrences w ON w.order_id=o.id
 JOIN users target ON target.id=NEW.to_supervisor_id JOIN users actor ON actor.id=NEW.actor_id
 WHERE o.id=NEW.order_id AND o.type='PM' AND o.status='draft' AND o.workflow_revision=1
 AND o.version=NEW.order_version AND w.executed_at IS NULL AND w.submitted_at IS NULL
 AND actor.active=1 AND actor.role IN ('head','manager')
 AND target.active=1 AND target.role='supervisor' AND target.specialty=w.specialty
 AND NEW.from_supervisor_id=COALESCE((SELECT r.to_supervisor_id FROM pm_responsibility_transfers r WHERE r.order_id=o.id ORDER BY r.order_version DESC LIMIT 1),w.supervisor_id)
 AND o.duration_minutes=0 AND trim(o.description)=''
 AND jsonb_array_length(COALESCE(o.body::jsonb->'participants','[]'::jsonb))=0
 AND jsonb_array_length(COALESCE(o.body::jsonb->'parts','[]'::jsonb))=0
 AND jsonb_array_length(COALESCE(o.body::jsonb->'checklist','[]'::jsonb))=0
 AND COALESCE(o.body::jsonb->>'description','')=''
 AND COALESCE(o.body::jsonb->>'startTime','')=''
 AND COALESCE(o.body::jsonb->>'endTime','')=''
 AND COALESCE((o.body::jsonb->>'durationMinutes')::numeric,0)=0
 AND COALESCE((o.body::jsonb->>'downtimeMinutes')::numeric,0)=0
 AND COALESCE((o.body::jsonb->>'productionStopped')::boolean,FALSE)=FALSE
 AND jsonb_typeof(COALESCE(o.body::jsonb->'pmResults','{}'::jsonb))='object'
 AND NOT EXISTS(SELECT 1 FROM jsonb_each(COALESCE(o.body::jsonb->'pmResults','{}'::jsonb)) r WHERE jsonb_typeof(r.value)<>'string' OR length(trim(r.value#>>'{}'))>0)
 AND NOT EXISTS(SELECT 1 FROM participants p WHERE p.order_id=o.id)
 AND NOT EXISTS(SELECT 1 FROM order_parts p WHERE p.order_id=o.id)
 AND NOT EXISTS(SELECT 1 FROM approvals a WHERE a.order_id=o.id)
 AND NOT EXISTS(SELECT 1 FROM participant_scores s WHERE s.order_id=o.id)
 AND NOT EXISTS(SELECT 1 FROM participant_head_scores s WHERE s.order_id=o.id)
 AND NOT EXISTS(SELECT 1 FROM participant_grading_policies s WHERE s.order_id=o.id)
 AND NOT EXISTS(SELECT 1 FROM completed_scores s WHERE s.order_id=o.id)
 AND NOT EXISTS(SELECT 1 FROM part_consumptions s WHERE s.order_id=o.id)
 AND NOT EXISTS(SELECT 1 FROM pm_submission_archive a WHERE a.order_id=o.id))
 THEN RAISE EXCEPTION 'PM responsibility transfer requires untouched draft and current active same-specialty supervisor' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END; $$;
DROP TRIGGER IF EXISTS guard_pm_responsibility_transfer_insert ON pm_responsibility_transfers;
CREATE TRIGGER guard_pm_responsibility_transfer_insert BEFORE INSERT ON pm_responsibility_transfers FOR EACH ROW EXECUTE FUNCTION pak_guard_pm_responsibility_transfer();
CREATE OR REPLACE FUNCTION pak_reject_pm_responsibility_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'PM responsibility transfer history is immutable' USING ERRCODE='23514'; END; $$;
DROP TRIGGER IF EXISTS guard_pm_responsibility_transfers_history ON pm_responsibility_transfers;
CREATE TRIGGER guard_pm_responsibility_transfers_history BEFORE UPDATE OR DELETE ON pm_responsibility_transfers FOR EACH ROW EXECUTE FUNCTION pak_reject_pm_responsibility_mutation();

-- Explicit initialization/migration owner only. No company rows or runtime DDL.
CREATE OR REPLACE FUNCTION pak_guard_insert_evidence() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_TABLE_NAME='participant_grading_policies' THEN
  IF NOT EXISTS(SELECT 1 FROM orders o JOIN participants p ON p.order_id=o.id
   JOIN users u ON u.id=p.person_id
   WHERE o.id=NEW.order_id AND o.workflow_revision=NEW.revision AND o.status='supervisor'
   AND p.person_id=NEW.person_id AND p.specialty=NEW.specialty
   AND u.active=1 AND u.specialty=NEW.specialty AND u.role=NEW.role_at_submission
   AND NEW.policy=CASE WHEN u.role='supervisor' THEN 'head-only-v1' ELSE 'two-stage-v1' END)
  THEN RAISE EXCEPTION 'invalid submitted participant grading policy' USING ERRCODE='23514'; END IF;
 ELSIF TG_TABLE_NAME='approvals' THEN
  IF NEW.revision<=0 OR NEW.stage NOT IN ('supervisor','head') OR NEW.decision NOT IN ('approve','return')
   OR (NEW.coefficient IS NOT NULL AND (NEW.coefficient<0 OR NEW.coefficient>3))
   OR NOT EXISTS(SELECT 1 FROM orders o WHERE o.id=NEW.order_id AND NEW.revision<=o.workflow_revision)
  THEN RAISE EXCEPTION 'invalid approval evidence' USING ERRCODE='23514'; END IF;
 ELSIF TG_TABLE_NAME='participant_scores' THEN
  IF NEW.actor_id=NEW.person_id OR EXISTS(SELECT 1 FROM participant_grading_policies gp WHERE gp.order_id=NEW.order_id AND gp.revision=NEW.revision AND gp.person_id=NEW.person_id AND gp.policy='head-only-v1') OR NOT EXISTS(SELECT 1 FROM approvals a JOIN orders o ON o.id=a.order_id
   WHERE a.id=NEW.approval_id AND a.order_id=NEW.order_id AND a.revision=NEW.revision
   AND a.stage='supervisor' AND a.decision='approve' AND a.specialty=NEW.specialty
   AND a.actor_id=NEW.actor_id AND (a.revision<o.workflow_revision OR EXISTS(SELECT 1 FROM participants p
    WHERE p.order_id=a.order_id AND p.person_id=NEW.person_id AND p.specialty=NEW.specialty)))
  THEN RAISE EXCEPTION 'participant score has no matching supervisor approval' USING ERRCODE='23514'; END IF;
 ELSIF TG_TABLE_NAME='participant_head_scores' THEN
  IF NEW.actor_id=NEW.person_id OR NEW.coefficient !~ '^[0-9](\.[0-9]{1,28})?$' OR CAST(NEW.coefficient AS NUMERIC)<0 OR CAST(NEW.coefficient AS NUMERIC)>(CASE WHEN EXISTS(SELECT 1 FROM participant_grading_policies gp WHERE gp.order_id=NEW.order_id AND gp.revision=NEW.revision AND gp.person_id=NEW.person_id AND gp.policy='head-only-v1') THEN 9 ELSE 3 END)
   OR NOT EXISTS(SELECT 1 FROM approvals a JOIN orders o ON o.id=a.order_id
    JOIN participants p ON p.order_id=o.id AND p.person_id=NEW.person_id AND p.specialty=NEW.specialty
    JOIN users u ON u.id=a.actor_id
    WHERE a.id=NEW.approval_id AND a.order_id=NEW.order_id AND a.revision=NEW.revision
    AND o.workflow_revision=NEW.revision AND o.status='head'
    AND a.stage='head' AND a.decision='approve' AND a.coefficient IS NULL
    AND a.actor_id=NEW.actor_id AND u.role='head' AND u.active=1)
  THEN RAISE EXCEPTION 'participant head score has no matching individual chief approval' USING ERRCODE='23514'; END IF;
 ELSIF TG_TABLE_NAME='part_consumptions' THEN
  IF NOT EXISTS(SELECT 1 FROM orders o JOIN approvals a ON a.order_id=o.id
   JOIN order_parts line ON line.order_id=o.id AND line.part_id=NEW.part_id
   WHERE o.id=NEW.order_id AND o.status='completed' AND o.workflow_revision=NEW.revision
   AND a.id=NEW.approval_id AND a.revision=NEW.revision AND a.stage='head' AND a.decision='approve'
   AND a.actor_id=NEW.actor_id AND line.quantity_units=NEW.quantity_units)
  THEN RAISE EXCEPTION 'consumption has no matching final approval' USING ERRCODE='23514'; END IF;
 ELSIF TG_TABLE_NAME='completed_scores' THEN
  IF NEW.revision<=0 OR (NEW.source IS NOT NULL AND NEW.source NOT IN ('individual','legacy-specialty','head-only'))
   OR (NEW.supervisor_coefficient IS NOT NULL AND (NEW.supervisor_coefficient<0 OR NEW.supervisor_coefficient>3))
   OR (NEW.head_coefficient IS NOT NULL AND (NEW.head_coefficient<0 OR NEW.head_coefficient>CASE WHEN NEW.source='head-only' THEN 9 ELSE 3 END))
   OR (NEW.score_cents IS NOT NULL AND NEW.score_cents<0)
   OR (NEW.source='head-only' AND (NEW.supervisor_coefficient IS NOT NULL OR NEW.head_coefficient IS NULL OR NEW.head_coefficient_exact IS NULL OR NEW.score_raw IS NULL OR NEW.score_cents IS NULL OR NOT EXISTS(SELECT 1 FROM participant_grading_policies gp WHERE gp.order_id=NEW.order_id AND gp.revision=NEW.revision AND gp.person_id=NEW.person_id AND gp.policy='head-only-v1')))
   OR (COALESCE(NEW.source,'')<>'head-only' AND EXISTS(SELECT 1 FROM participant_grading_policies gp WHERE gp.order_id=NEW.order_id AND gp.revision=NEW.revision AND gp.person_id=NEW.person_id AND gp.policy='head-only-v1'))
   OR (NEW.head_coefficient_exact IS NOT NULL AND NOT EXISTS(SELECT 1 FROM participant_head_scores h
    WHERE h.order_id=NEW.order_id AND h.revision=NEW.revision AND h.person_id=NEW.person_id
    AND h.coefficient=NEW.head_coefficient_exact AND CAST(h.coefficient AS NUMERIC)=NEW.head_coefficient))
   OR (NEW.head_coefficient_exact IS NULL AND EXISTS(SELECT 1 FROM participant_head_scores h
    WHERE h.order_id=NEW.order_id AND h.revision=NEW.revision AND h.person_id=NEW.person_id))
   OR (NEW.head_coefficient_exact IS NULL AND NEW.head_coefficient IS NOT NULL AND NOT EXISTS(SELECT 1 FROM approvals a
    WHERE a.order_id=NEW.order_id AND a.revision=NEW.revision AND a.stage='head' AND a.decision='approve' AND a.coefficient=NEW.head_coefficient))
   OR NOT EXISTS(SELECT 1 FROM orders o JOIN participants p ON p.order_id=o.id
    WHERE o.id=NEW.order_id AND o.status='completed' AND o.workflow_revision=NEW.revision AND p.person_id=NEW.person_id)
   OR (NEW.source='individual' AND NOT EXISTS(SELECT 1 FROM participant_scores s
    WHERE s.order_id=NEW.order_id AND s.revision=NEW.revision AND s.person_id=NEW.person_id AND s.coefficient=NEW.supervisor_coefficient))
   OR (NEW.source='legacy-specialty' AND NOT EXISTS(SELECT 1 FROM approvals a JOIN participants p ON p.order_id=a.order_id
    WHERE a.order_id=NEW.order_id AND a.revision=NEW.revision AND a.stage='supervisor' AND a.decision='approve'
    AND a.specialty=p.specialty AND p.person_id=NEW.person_id AND a.coefficient=NEW.supervisor_coefficient))
  THEN RAISE EXCEPTION 'invalid completed score evidence' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END; $$;
DROP TRIGGER IF EXISTS guard_participant_grading_policy_insert ON participant_grading_policies;
CREATE TRIGGER guard_participant_grading_policy_insert BEFORE INSERT ON participant_grading_policies FOR EACH ROW EXECUTE FUNCTION pak_guard_insert_evidence();
DROP TRIGGER IF EXISTS guard_approval_evidence_insert ON approvals;
CREATE TRIGGER guard_approval_evidence_insert BEFORE INSERT ON approvals FOR EACH ROW EXECUTE FUNCTION pak_guard_insert_evidence();
DROP TRIGGER IF EXISTS guard_participant_score_evidence_insert ON participant_scores;
CREATE TRIGGER guard_participant_score_evidence_insert BEFORE INSERT ON participant_scores FOR EACH ROW EXECUTE FUNCTION pak_guard_insert_evidence();
DROP TRIGGER IF EXISTS guard_participant_head_score_evidence_insert ON participant_head_scores;
CREATE TRIGGER guard_participant_head_score_evidence_insert BEFORE INSERT ON participant_head_scores FOR EACH ROW EXECUTE FUNCTION pak_guard_insert_evidence();
DROP TRIGGER IF EXISTS guard_consumption_evidence_insert ON part_consumptions;
CREATE TRIGGER guard_consumption_evidence_insert BEFORE INSERT ON part_consumptions FOR EACH ROW EXECUTE FUNCTION pak_guard_insert_evidence();
DROP TRIGGER IF EXISTS guard_completed_score_evidence_insert ON completed_scores;
CREATE TRIGGER guard_completed_score_evidence_insert BEFORE INSERT ON completed_scores FOR EACH ROW EXECUTE FUNCTION pak_guard_insert_evidence();
CREATE OR REPLACE FUNCTION pak_reject_evidence_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'published evidence is immutable' USING ERRCODE='23514'; END; $$;
DROP TRIGGER IF EXISTS guard_participant_grading_policies_history ON participant_grading_policies;
CREATE TRIGGER guard_participant_grading_policies_history BEFORE UPDATE OR DELETE ON participant_grading_policies FOR EACH ROW EXECUTE FUNCTION pak_reject_evidence_mutation();
DROP TRIGGER IF EXISTS guard_approvals_history ON approvals;
CREATE TRIGGER guard_approvals_history BEFORE UPDATE OR DELETE ON approvals FOR EACH ROW EXECUTE FUNCTION pak_reject_evidence_mutation();
DROP TRIGGER IF EXISTS guard_participant_scores_history ON participant_scores;
CREATE TRIGGER guard_participant_scores_history BEFORE UPDATE OR DELETE ON participant_scores FOR EACH ROW EXECUTE FUNCTION pak_reject_evidence_mutation();
DROP TRIGGER IF EXISTS guard_participant_head_scores_history ON participant_head_scores;
CREATE TRIGGER guard_participant_head_scores_history BEFORE UPDATE OR DELETE ON participant_head_scores FOR EACH ROW EXECUTE FUNCTION pak_reject_evidence_mutation();
DROP TRIGGER IF EXISTS guard_request_claims_history ON request_claims;
CREATE TRIGGER guard_request_claims_history BEFORE UPDATE OR DELETE ON request_claims FOR EACH ROW EXECUTE FUNCTION pak_reject_evidence_mutation();
DROP TRIGGER IF EXISTS guard_audit_history ON audit;
CREATE TRIGGER guard_audit_history BEFORE UPDATE OR DELETE ON audit FOR EACH ROW EXECUTE FUNCTION pak_reject_evidence_mutation();

CREATE OR REPLACE FUNCTION pak_guard_pm_change_request() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'PM change review history is immutable' USING ERRCODE='23514'; END IF;
 IF TG_OP='INSERT' THEN
  IF NEW.status<>'pending' OR NEW.version<>1 OR NEW.reviewer_id IS NOT NULL OR NEW.decided_at IS NOT NULL OR NEW.result IS NOT NULL
   OR NOT EXISTS(SELECT 1 FROM users u WHERE u.id=NEW.requester_id AND u.active=1 AND u.role IN ('head','manager','planner','supervisor','admin'))
  THEN RAISE EXCEPTION 'new PM change request must start pending' USING ERRCODE='23514'; END IF;
 ELSE
  IF OLD.status<>'pending' OR NEW.status NOT IN ('approved','rejected') OR NEW.version<>OLD.version+1
   OR ROW(NEW.id,NEW.request_key,NEW.requester_id,NEW.operation,NEW.entity_id,NEW.base_version,NEW.payload,NEW.payload_hash,NEW.created_at)
      IS DISTINCT FROM ROW(OLD.id,OLD.request_key,OLD.requester_id,OLD.operation,OLD.entity_id,OLD.base_version,OLD.payload,OLD.payload_hash,OLD.created_at)
   OR NEW.decided_at IS NULL OR NOT EXISTS(SELECT 1 FROM users u WHERE u.id=NEW.reviewer_id AND u.active=1 AND u.role='head')
  THEN RAISE EXCEPTION 'PM change decision must be immutable and approved by a chief' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END; $$;
DROP TRIGGER IF EXISTS guard_pm_change_request_history ON pm_change_requests;
CREATE TRIGGER guard_pm_change_request_history BEFORE INSERT OR UPDATE OR DELETE ON pm_change_requests FOR EACH ROW EXECUTE FUNCTION pak_guard_pm_change_request();

$pak_ddl$;
 EXECUTE format('GRANT SELECT,INSERT,UPDATE,DELETE ON public.participant_grading_policies,public.pm_responsibility_transfers TO %I',runtime_user);
 EXECUTE format('GRANT EXECUTE ON FUNCTION public.pak_guard_insert_evidence(),public.pak_reject_evidence_mutation(),public.pak_guard_pm_change_request(),public.pak_guard_pm_responsibility_transfer(),public.pak_reject_pm_responsibility_mutation() TO %I',runtime_user);
 UPDATE schema_version SET version=9 WHERE version=8;
 IF NOT FOUND THEN RAISE EXCEPTION 'schema marker publication failed'; END IF;
 RAISE NOTICE 'schema 8 upgraded to 9; existing data and optional OEE preserved';
END;
$pak_upgrade$;
