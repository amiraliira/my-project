-- Schema 12: explicit NEW-database initialization only.
-- Schema 7 generated from a verified consistent SQLite snapshot. No company rows or passwords.
-- Explicit initialization of a NEW target database only; runtime must not migrate live data.

CREATE TABLE assets(id TEXT PRIMARY KEY,code TEXT NOT NULL UNIQUE,name TEXT NOT NULL,hall TEXT NOT NULL,line TEXT NOT NULL,body TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1);

CREATE TABLE legacy_records(id TEXT PRIMARY KEY,body TEXT NOT NULL,imported_at TEXT NOT NULL);

CREATE TABLE parts(id TEXT PRIMARY KEY,code TEXT NOT NULL UNIQUE,name TEXT NOT NULL,specification TEXT NOT NULL,unit TEXT NOT NULL,unit_cost NUMERIC NULL CHECK(unit_cost>=0), version INTEGER NOT NULL DEFAULT 1);

CREATE TABLE pm_templates(id TEXT PRIMARY KEY,title TEXT NOT NULL,specialty TEXT NOT NULL,hall TEXT NOT NULL,period TEXT NOT NULL,version INTEGER NOT NULL CHECK(version>0),mapping_status TEXT NOT NULL,body TEXT NOT NULL);

CREATE TABLE schema_version(version INTEGER NOT NULL);

CREATE TABLE users(id TEXT PRIMARY KEY,code TEXT NOT NULL UNIQUE,name TEXT NOT NULL,role TEXT NOT NULL,specialty TEXT NOT NULL,active INTEGER NOT NULL,password_hash TEXT NOT NULL,session_epoch INTEGER NOT NULL);

CREATE TABLE audit(id TEXT PRIMARY KEY,actor_id TEXT NULL REFERENCES users(id),actor_name TEXT NOT NULL,action TEXT NOT NULL,entity TEXT NOT NULL,entity_id TEXT NOT NULL,details TEXT NOT NULL,created_at TEXT NOT NULL);

CREATE TABLE orders(id TEXT PRIMARY KEY,number TEXT NOT NULL UNIQUE,version INTEGER NOT NULL,workflow_revision INTEGER NOT NULL,type TEXT NOT NULL CONSTRAINT ck_orders_work_type CHECK(type IN ('PM','EM','CM','PROJECT','GENERAL')),status TEXT NOT NULL,asset_id TEXT NOT NULL REFERENCES assets(id),created_by TEXT NOT NULL REFERENCES users(id),assignee_id TEXT NULL REFERENCES users(id),work_date TEXT NOT NULL,due_date TEXT NULL,body TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL, duration_minutes INTEGER NOT NULL DEFAULT 0, title TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '');

CREATE TABLE part_consumption_totals(part_id TEXT PRIMARY KEY REFERENCES parts(id),quantity_units BIGINT NOT NULL CHECK(quantity_units>0),report_count BIGINT NOT NULL CHECK(report_count>0));

CREATE TABLE plans(id TEXT PRIMARY KEY,asset_id TEXT NOT NULL REFERENCES assets(id),next_due TEXT NOT NULL,interval_days INTEGER NOT NULL CHECK(interval_days>0),active INTEGER NOT NULL,version INTEGER NOT NULL,body TEXT NOT NULL);

CREATE TABLE pm_setup_requests(actor_id TEXT NOT NULL REFERENCES users(id),request_key TEXT NOT NULL,payload_hash TEXT NOT NULL,body TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(actor_id,request_key));

CREATE TABLE pm_template_revisions(template_id TEXT NOT NULL REFERENCES pm_templates(id),revision INTEGER NOT NULL,body TEXT NOT NULL,reason TEXT NOT NULL,actor_id TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,PRIMARY KEY(template_id,revision));

CREATE TABLE pm_weekly_plans(id TEXT PRIMARY KEY,template_id TEXT NOT NULL REFERENCES pm_templates(id),asset_id TEXT NULL REFERENCES assets(id),supervisor_id TEXT NULL REFERENCES users(id),specialty TEXT NOT NULL,hall TEXT NOT NULL,version INTEGER NOT NULL CHECK(version>0),active INTEGER NOT NULL CHECK(active IN (0,1)),mapping_confirmed INTEGER NOT NULL CHECK(mapping_confirmed IN (0,1)),start_date TEXT NOT NULL,cursor_month INTEGER NOT NULL,next_scan TEXT NOT NULL,body TEXT NOT NULL);

CREATE TABLE sessions(id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),epoch INTEGER NOT NULL,expires TEXT NOT NULL);

CREATE TABLE stock_movements(id TEXT PRIMARY KEY,part_id TEXT NOT NULL REFERENCES parts(id),quantity NUMERIC NOT NULL,kind TEXT NOT NULL,reference TEXT NOT NULL,note TEXT NOT NULL,actor_id TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL, quantity_units BIGINT NOT NULL DEFAULT 0);

CREATE TABLE approvals(id TEXT PRIMARY KEY,order_id TEXT NOT NULL REFERENCES orders(id),revision INTEGER NOT NULL,stage TEXT NOT NULL,specialty TEXT NOT NULL,decision TEXT NOT NULL,coefficient NUMERIC NULL,reason TEXT NOT NULL,actor_id TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL);

CREATE TABLE completed_scores(order_id TEXT NOT NULL REFERENCES orders(id),revision INTEGER NOT NULL,person_id TEXT NOT NULL REFERENCES users(id),supervisor_coefficient NUMERIC NULL,head_coefficient NUMERIC NULL,score_raw TEXT NULL,score_cents BIGINT NULL,source TEXT NULL,head_coefficient_exact TEXT NULL,PRIMARY KEY(order_id,revision,person_id));

CREATE TABLE order_parts(order_id TEXT NOT NULL REFERENCES orders(id),part_id TEXT NOT NULL REFERENCES parts(id),quantity NUMERIC NOT NULL CHECK(quantity>0), quantity_units BIGINT NOT NULL DEFAULT 0,PRIMARY KEY(order_id,part_id));

CREATE TABLE participants(order_id TEXT NOT NULL REFERENCES orders(id),person_id TEXT NOT NULL REFERENCES users(id),minutes INTEGER NOT NULL CHECK(minutes>0),specialty TEXT NOT NULL,PRIMARY KEY(order_id,person_id));

CREATE TABLE pm_occurrences(plan_id TEXT NOT NULL REFERENCES plans(id),due_date TEXT NOT NULL,order_id TEXT NOT NULL REFERENCES orders(id),PRIMARY KEY(plan_id,due_date));

CREATE TABLE pm_plan_revisions(plan_id TEXT NOT NULL REFERENCES pm_weekly_plans(id),revision INTEGER NOT NULL,body TEXT NOT NULL,reason TEXT NOT NULL,actor_id TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,PRIMARY KEY(plan_id,revision));

CREATE TABLE pm_weekly_assets(order_id TEXT NOT NULL REFERENCES orders(id),asset_id TEXT NOT NULL REFERENCES assets(id),PRIMARY KEY(order_id,asset_id));

CREATE TABLE request_claims(actor_id TEXT NOT NULL REFERENCES users(id),request_key TEXT NOT NULL,order_id TEXT NOT NULL REFERENCES orders(id),payload_hash TEXT NOT NULL,PRIMARY KEY(actor_id,request_key));

CREATE TABLE stock_request_claims(actor_id TEXT NOT NULL REFERENCES users(id),request_key TEXT NOT NULL,movement_id TEXT NOT NULL REFERENCES stock_movements(id),payload_hash TEXT NOT NULL,PRIMARY KEY(actor_id,request_key));

CREATE TABLE part_consumptions(id TEXT PRIMARY KEY,order_id TEXT NOT NULL REFERENCES orders(id),part_id TEXT NOT NULL REFERENCES parts(id),approval_id TEXT NOT NULL REFERENCES approvals(id),revision INTEGER NOT NULL CHECK(revision>0),quantity_units BIGINT NOT NULL CHECK(quantity_units>0),part_code TEXT NOT NULL,part_name TEXT NOT NULL,unit TEXT NOT NULL,order_number TEXT NOT NULL,executed_at TEXT NOT NULL,approved_at TEXT NOT NULL,actor_id TEXT NOT NULL REFERENCES users(id),actor_name TEXT NOT NULL,UNIQUE(order_id,part_id));

CREATE TABLE participant_scores(order_id TEXT NOT NULL REFERENCES orders(id),revision INTEGER NOT NULL,person_id TEXT NOT NULL REFERENCES users(id),specialty TEXT NOT NULL,coefficient NUMERIC NOT NULL CHECK(coefficient>=0 AND coefficient<=3),actor_id TEXT NOT NULL REFERENCES users(id),approval_id TEXT NOT NULL REFERENCES approvals(id),created_at TEXT NOT NULL,PRIMARY KEY(order_id,revision,person_id));

CREATE TABLE pm_weekly_occurrences(plan_id TEXT NOT NULL REFERENCES pm_weekly_plans(id),persian_year INTEGER NOT NULL,month INTEGER NOT NULL CHECK(month BETWEEN 1 AND 12),week INTEGER NOT NULL CHECK(week BETWEEN 1 AND 4),order_id TEXT NOT NULL UNIQUE REFERENCES orders(id),plan_version INTEGER NOT NULL,template_id TEXT NOT NULL,template_version INTEGER NOT NULL,start_utc TEXT NOT NULL,end_utc TEXT NOT NULL,specialty TEXT NOT NULL,supervisor_id TEXT NOT NULL REFERENCES users(id),submitted_at TEXT NULL,executed_at TEXT NULL,PRIMARY KEY(plan_id,persian_year,month,week),FOREIGN KEY(template_id,template_version) REFERENCES pm_template_revisions(template_id,revision),FOREIGN KEY(plan_id,plan_version) REFERENCES pm_plan_revisions(plan_id,revision));

CREATE INDEX ix_approval_order ON approvals(order_id,revision);
CREATE INDEX ix_approvals_stage ON approvals(order_id,revision,stage,decision,specialty,created_at DESC,id DESC);
CREATE INDEX ix_assets_location ON assets(hall,line,code,id);
CREATE INDEX ix_assets_name ON assets(name,id);
CREATE INDEX ix_audit_created ON audit(created_at DESC,id DESC);
CREATE INDEX ix_audit_entity ON audit(entity,entity_id,created_at,id);
CREATE INDEX ix_completed_scores_person ON completed_scores(person_id,order_id,revision);
CREATE INDEX ix_consumption_date ON part_consumptions(executed_at DESC,id DESC);
CREATE INDEX ix_consumption_part_date ON part_consumptions(part_id,executed_at DESC,id DESC);
CREATE INDEX ix_movements_created ON stock_movements(created_at DESC,id DESC);
CREATE INDEX ix_movements_part ON stock_movements(part_id,created_at);
CREATE INDEX ix_orders_asset_created ON orders(asset_id,created_at DESC,id DESC);
CREATE INDEX ix_orders_assignee ON orders(assignee_id,status,created_at DESC,id DESC);
CREATE INDEX ix_orders_created ON orders(created_at DESC,id DESC);
CREATE INDEX ix_orders_creator ON orders(created_by,created_at DESC,id DESC);
CREATE INDEX ix_orders_inbox ON orders(status,updated_at,created_at,id);
CREATE INDEX ix_orders_performance_date ON orders(status,work_date,id);
CREATE INDEX ix_orders_status ON orders(status,due_date);
CREATE INDEX ix_participant_scores_approval ON participant_scores(approval_id);
CREATE INDEX ix_participants_person ON participants(person_id,specialty,order_id);
CREATE INDEX ix_participants_specialty ON participants(specialty,order_id);
CREATE INDEX ix_parts_name ON parts(name,id);
CREATE INDEX ix_plans_due ON plans(active,next_due,id);
CREATE INDEX ix_pm_templates_filter ON pm_templates(specialty,hall,title,id);
CREATE INDEX ix_pm_weekly_assets_asset ON pm_weekly_assets(asset_id,order_id);
CREATE INDEX ix_pm_weekly_deadline ON pm_weekly_occurrences(end_utc,order_id);
CREATE INDEX ix_pm_weekly_due ON pm_weekly_plans(active,next_scan,id);
CREATE INDEX ix_pm_weekly_owner ON pm_weekly_occurrences(supervisor_id,end_utc,order_id);
CREATE INDEX ix_sessions_expires ON sessions(expires);
CREATE INDEX ix_sessions_user ON sessions(user_id,expires DESC,id);
CREATE INDEX ix_users_specialty_active ON users(specialty,active,id);


-- Per-person chief assessments and controlled PM publication (schema 7).
CREATE TABLE participant_head_scores(order_id TEXT NOT NULL REFERENCES orders(id),revision INTEGER NOT NULL CHECK(revision>0),person_id TEXT NOT NULL REFERENCES users(id),specialty TEXT NOT NULL,coefficient TEXT NOT NULL CHECK(length(coefficient) BETWEEN 1 AND 31 AND CAST(coefficient AS NUMERIC)>=0 AND CAST(coefficient AS NUMERIC)<=3),actor_id TEXT NOT NULL REFERENCES users(id),approval_id TEXT NOT NULL REFERENCES approvals(id),created_at TEXT NOT NULL,PRIMARY KEY(order_id,revision,person_id));
CREATE INDEX ix_participant_head_scores_approval ON participant_head_scores(approval_id);
CREATE TABLE pm_change_requests(id TEXT PRIMARY KEY,request_key TEXT NOT NULL,requester_id TEXT NOT NULL REFERENCES users(id),operation TEXT NOT NULL CHECK(operation IN ('create-template','edit-template','design-template','retire-template','restore-template','edit-plan')),entity_id TEXT NOT NULL,base_version INTEGER NULL,payload TEXT NOT NULL,payload_hash TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ('pending','approved','rejected')),version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),reviewer_id TEXT NULL REFERENCES users(id),decision_reason TEXT NOT NULL DEFAULT '',result TEXT NULL,created_at TEXT NOT NULL,decided_at TEXT NULL,UNIQUE(requester_id,request_key));
CREATE INDEX ix_pm_changes_status ON pm_change_requests(status,created_at DESC,id DESC);
CREATE INDEX ix_pm_changes_requester ON pm_change_requests(requester_id,created_at DESC,id DESC);

INSERT INTO schema_version(version) VALUES(12);

-- Register exact score aggregate/functions from reporting_service.POSTGRESQL_FUNCTIONS before use.

-- Additive schema-7 to schema-8 objects. Explicit migration owner only.
CREATE TABLE IF NOT EXISTS pm_submission_archive(
 id TEXT PRIMARY KEY,
 order_id TEXT NOT NULL REFERENCES orders(id),
 workflow_revision INTEGER NOT NULL CHECK(workflow_revision>0),
 template_id TEXT NOT NULL,
 template_version INTEGER NOT NULL CHECK(template_version>0),
 plan_id TEXT NOT NULL,
 plan_version INTEGER NOT NULL CHECK(plan_version>0),
 asset_id TEXT NOT NULL REFERENCES assets(id),
 performer_id TEXT NOT NULL REFERENCES users(id),
 supervisor_id TEXT NOT NULL REFERENCES users(id),
 submitted_by TEXT NOT NULL REFERENCES users(id),
 number TEXT NOT NULL,
 title TEXT NOT NULL,
 hall TEXT NOT NULL,
 asset_code TEXT NOT NULL,
 asset_name TEXT NOT NULL,
 performer_name TEXT NOT NULL,
 specialty TEXT NOT NULL,
 work_date TEXT NOT NULL,
 executed_at TEXT NOT NULL,
 submitted_at TEXT NOT NULL,
 body TEXT NOT NULL,
 body_sha256 TEXT NOT NULL CHECK(length(body_sha256)=64),
 evidence_source TEXT NOT NULL CHECK(evidence_source IN ('submission-snapshot','legacy-reconstructed','supervisor-correction-snapshot')),
 UNIQUE(order_id,workflow_revision),
 FOREIGN KEY(template_id,template_version) REFERENCES pm_template_revisions(template_id,revision),
 FOREIGN KEY(plan_id,plan_version) REFERENCES pm_plan_revisions(plan_id,revision)
);
CREATE INDEX IF NOT EXISTS ix_pm_archive_submitted ON pm_submission_archive(submitted_at DESC,id DESC);
CREATE INDEX IF NOT EXISTS ix_pm_archive_work_date ON pm_submission_archive(work_date DESC,submitted_at DESC,id DESC);
CREATE INDEX IF NOT EXISTS ix_pm_archive_performer ON pm_submission_archive(performer_id,work_date DESC,submitted_at DESC,id DESC);
CREATE INDEX IF NOT EXISTS ix_pm_archive_template ON pm_submission_archive(template_id,work_date DESC,submitted_at DESC,id DESC);
CREATE INDEX IF NOT EXISTS ix_pm_archive_hall ON pm_submission_archive(hall,work_date DESC,submitted_at DESC,id DESC);
CREATE INDEX IF NOT EXISTS ix_pm_archive_asset ON pm_submission_archive(asset_id,work_date DESC,submitted_at DESC,id DESC);

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

-- Schema 11: technical form classification is distinct from the responsible execution team.
CREATE OR REPLACE FUNCTION pak_guard_pm_responsibility_transfer() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 -- Serialize direct SQL inserts with assignment, submission and other transfers.
 PERFORM 1 FROM orders WHERE id=NEW.order_id FOR UPDATE;
 IF NOT EXISTS(SELECT 1 FROM orders o JOIN pm_weekly_occurrences w ON w.order_id=o.id
 JOIN users target ON target.id=NEW.to_supervisor_id JOIN users actor ON actor.id=NEW.actor_id
 WHERE o.id=NEW.order_id AND o.type='PM' AND o.status='draft' AND o.workflow_revision=1
 AND o.version=NEW.order_version AND w.executed_at IS NULL AND w.submitted_at IS NULL
 AND actor.active=1 AND actor.role IN ('head','manager')
 AND target.active=1 AND target.role='supervisor' AND length(trim(target.specialty))>0
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
 THEN RAISE EXCEPTION 'PM responsibility transfer requires untouched draft and current active responsible supervisor' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END; $$;
DROP TRIGGER IF EXISTS guard_pm_responsibility_transfer_insert ON pm_responsibility_transfers;
CREATE TRIGGER guard_pm_responsibility_transfer_insert BEFORE INSERT ON pm_responsibility_transfers FOR EACH ROW EXECUTE FUNCTION pak_guard_pm_responsibility_transfer();

-- Capture and immutable evidence constraints; runtime never executes DDL.
CREATE OR REPLACE FUNCTION pak_guard_pm_archive_insert() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.body_sha256<>encode(sha256(convert_to(NEW.body,'UTF8')),'hex')
 THEN RAISE EXCEPTION 'PM archive snapshot hash mismatch' USING ERRCODE='23514'; END IF;
 IF NOT EXISTS(SELECT 1 FROM orders o JOIN pm_weekly_occurrences w ON w.order_id=o.id
  JOIN pm_weekly_plans p ON p.id=w.plan_id
  WHERE o.id=NEW.order_id AND o.type='PM' AND o.workflow_revision>=NEW.workflow_revision
   AND w.template_id=NEW.template_id AND w.template_version=NEW.template_version
   AND w.plan_id=NEW.plan_id AND w.plan_version=NEW.plan_version
   AND p.template_id=w.template_id AND w.supervisor_id=NEW.supervisor_id
   AND w.specialty=NEW.specialty AND o.asset_id=NEW.asset_id)
 THEN RAISE EXCEPTION 'PM archive identity has no matching issued form and plan' USING ERRCODE='23514'; END IF;
 IF NEW.evidence_source IN ('submission-snapshot','supervisor-correction-snapshot') AND NOT EXISTS(
  SELECT 1 FROM orders o JOIN participants p ON p.order_id=o.id
  JOIN users u ON u.id=p.person_id
  JOIN pm_weekly_occurrences routed ON routed.order_id=o.id
  JOIN users responsible ON responsible.id=COALESCE((SELECT route.to_supervisor_id FROM pm_responsibility_transfers route WHERE route.order_id=o.id ORDER BY route.order_version DESC LIMIT 1),routed.supervisor_id)
  WHERE o.id=NEW.order_id AND o.workflow_revision=NEW.workflow_revision AND o.status='supervisor'
   AND o.assignee_id=NEW.performer_id AND p.person_id=NEW.performer_id AND p.specialty=u.specialty
   AND responsible.active=1 AND responsible.role='supervisor' AND responsible.specialty=p.specialty AND length(trim(p.specialty))>0
   AND jsonb_typeof(NEW.body::jsonb->'execution'->'participants')='array'
   AND jsonb_array_length(NEW.body::jsonb->'execution'->'participants')=1
   AND NEW.body::jsonb->'execution'->'participants'->0->>'personId'=p.person_id
   AND NEW.body::jsonb->'execution'->'participants'->0->>'specialty'=p.specialty
   AND (NEW.body::jsonb->'execution'->'participants'->0->>'minutes')::INTEGER=p.minutes
   AND (NEW.evidence_source='supervisor-correction-snapshot' OR NEW.submitted_by=NEW.performer_id)
   AND u.role='technician' AND u.active=1
   AND o.work_date=NEW.work_date
   AND NEW.body::jsonb->'results'=o.body::jsonb->'pmResults'
   AND NEW.body::jsonb->'execution'->>'workDate'=o.work_date
   AND NEW.body::jsonb->'execution'->>'startTime'=o.body::jsonb->>'startTime'
   AND NEW.body::jsonb->'execution'->>'endTime'=o.body::jsonb->>'endTime'
   AND NEW.body::jsonb->'execution'->>'durationMinutes'=o.body::jsonb->>'durationMinutes'
   AND EXISTS(SELECT 1 FROM pm_weekly_occurrences w WHERE w.order_id=o.id AND w.executed_at=NEW.executed_at AND w.submitted_at=NEW.submitted_at)
   AND (SELECT COUNT(*) FROM participants n WHERE n.order_id=o.id)=1)
 THEN RAISE EXCEPTION 'PM archive snapshot must match the submitted execution' USING ERRCODE='23514'; END IF;
 IF NEW.evidence_source='legacy-reconstructed' AND NOT pg_has_role(current_user,
   (SELECT relowner FROM pg_class WHERE oid='pm_submission_archive'::regclass),'MEMBER')
 THEN RAISE EXCEPTION 'Legacy reconstruction requires the explicit migration owner' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END; $$;
DROP TRIGGER IF EXISTS guard_pm_archive_insert ON pm_submission_archive;
CREATE TRIGGER guard_pm_archive_insert BEFORE INSERT ON pm_submission_archive FOR EACH ROW EXECUTE FUNCTION pak_guard_pm_archive_insert();
CREATE OR REPLACE FUNCTION pak_reject_evidence_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'published evidence is immutable' USING ERRCODE='23514'; END; $$;
DROP TRIGGER IF EXISTS guard_pm_archive_history ON pm_submission_archive;
CREATE TRIGGER guard_pm_archive_history BEFORE UPDATE OR DELETE ON pm_submission_archive FOR EACH ROW EXECUTE FUNCTION pak_reject_evidence_mutation();

-- Schema 10: corrections are explicit supervisor evidence, never a forged
-- technician resubmission. The prior immutable archive remains the base.
CREATE OR REPLACE FUNCTION pak_guard_pm_archive_correction_insert() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.evidence_source<>'supervisor-correction-snapshot' THEN RETURN NEW; END IF;
 IF NEW.body::jsonb->>'provenance' IS DISTINCT FROM 'supervisor-correction-snapshot'
  OR jsonb_typeof(NEW.body::jsonb->'correction') IS DISTINCT FROM 'object'
  OR jsonb_typeof(NEW.body::jsonb->'correction'->'baseRevision') IS DISTINCT FROM 'number'
  OR (NEW.body::jsonb->'correction'->>'baseRevision') !~ '^[1-9][0-9]*$'
  OR length(trim(COALESCE(NEW.body::jsonb->'correction'->>'reason',''))) NOT BETWEEN 3 AND 2000
  OR NOT EXISTS(
   SELECT 1 FROM orders o JOIN pm_weekly_occurrences w ON w.order_id=o.id
   JOIN users actor ON actor.id=NEW.submitted_by
   JOIN pm_submission_archive base ON base.id=NEW.body::jsonb->'correction'->>'baseArchiveId'
   JOIN participants participant ON participant.order_id=o.id AND participant.person_id=NEW.performer_id
   WHERE o.id=NEW.order_id AND o.status='supervisor' AND o.workflow_revision=NEW.workflow_revision
    AND actor.active=1 AND actor.role='supervisor' AND actor.specialty=participant.specialty
    AND actor.id=COALESCE((SELECT t.to_supervisor_id FROM pm_responsibility_transfers t
      WHERE t.order_id=o.id ORDER BY t.order_version DESC LIMIT 1),w.supervisor_id)
    AND o.assignee_id=NEW.performer_id AND participant.specialty=actor.specialty AND length(trim(participant.specialty))>0
    AND participant.minutes=o.duration_minutes
    AND base.order_id=o.id AND base.workflow_revision=NEW.workflow_revision-1
    AND base.workflow_revision=CAST(NEW.body::jsonb->'correction'->>'baseRevision' AS INTEGER)
    AND base.performer_id=NEW.performer_id AND base.template_id=NEW.template_id
    AND base.template_version=NEW.template_version AND base.plan_id=NEW.plan_id
    AND base.plan_version=NEW.plan_version AND base.asset_id=NEW.asset_id
    AND base.work_date=NEW.work_date
    AND base.body::jsonb->'results'=NEW.body::jsonb->'results'
    AND base.body::jsonb->'window'=NEW.body::jsonb->'window'
    AND COALESCE(base.body::jsonb->'responsibility','null'::jsonb)=COALESCE(NEW.body::jsonb->'responsibility','null'::jsonb)
    AND base.body::jsonb->'equipmentMembers'=NEW.body::jsonb->'equipmentMembers'
    AND NEW.body::jsonb->'execution'->>'description'=o.description
    AND NEW.work_date=o.work_date
    AND NEW.body::jsonb->'execution'->>'workDate'=o.work_date
    AND NEW.body::jsonb->'execution'->>'startTime'=o.body::jsonb->>'startTime'
    AND NEW.body::jsonb->'execution'->>'endTime'=o.body::jsonb->>'endTime'
    AND CAST(NEW.body::jsonb->'execution'->>'durationMinutes' AS INTEGER)=o.duration_minutes
    AND NEW.body::jsonb->'results'=o.body::jsonb->'pmResults'
    AND w.executed_at=NEW.executed_at AND w.submitted_at=NEW.submitted_at
    AND jsonb_typeof(NEW.body::jsonb->'execution'->'parts')='array'
    AND jsonb_array_length(NEW.body::jsonb->'execution'->'parts')=(SELECT COUNT(*) FROM order_parts op WHERE op.order_id=o.id)
    AND (SELECT COUNT(DISTINCT q->>'partId') FROM jsonb_array_elements(NEW.body::jsonb->'execution'->'parts') q)=jsonb_array_length(NEW.body::jsonb->'execution'->'parts')
    AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.body::jsonb->'execution'->'parts') q
      WHERE jsonb_typeof(q->'quantity') IS DISTINCT FROM 'number' OR NOT EXISTS(
       SELECT 1 FROM order_parts op JOIN parts master ON master.id=op.part_id
       WHERE op.order_id=o.id AND op.part_id=q->>'partId'
        AND CAST(q->>'quantity' AS NUMERIC)*1000000=op.quantity_units
        AND q->>'code'=master.code AND q->>'name'=master.name AND q->>'unit'=master.unit))
    AND jsonb_array_length(NEW.body::jsonb->'execution'->'participants')=1
    AND NEW.body::jsonb->'execution'->'participants'->0->>'personId'=NEW.performer_id
    AND CAST(NEW.body::jsonb->'execution'->'participants'->0->>'minutes' AS INTEGER)=participant.minutes
    AND EXISTS(SELECT 1 FROM audit event WHERE event.entity='order' AND event.entity_id=o.id
     AND event.action='review-correction' AND event.actor_id=NEW.submitted_by AND event.created_at=NEW.submitted_at
     AND event.details::jsonb->>'fromRevision'=NEW.body::jsonb->'correction'->>'baseRevision'
     AND event.details::jsonb->>'toRevision'=CAST(NEW.workflow_revision AS TEXT)
     AND event.details::jsonb->>'reason'=NEW.body::jsonb->'correction'->>'reason')
    AND NOT EXISTS(SELECT 1 FROM approvals a WHERE a.order_id=o.id AND a.revision=o.workflow_revision)
  )
 THEN RAISE EXCEPTION 'PM supervisor correction must preserve its immutable base and match the current authorized review' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END; $$;
DROP TRIGGER IF EXISTS guard_pm_archive_correction_insert ON pm_submission_archive;
CREATE TRIGGER guard_pm_archive_correction_insert BEFORE INSERT ON pm_submission_archive
 FOR EACH ROW EXECUTE FUNCTION pak_guard_pm_archive_correction_insert();

CREATE INDEX IF NOT EXISTS ix_pm_weekly_plan_owner_template ON pm_weekly_plans(supervisor_id,template_id);
CREATE INDEX IF NOT EXISTS ix_pm_weekly_template_order ON pm_weekly_occurrences(template_id,order_id);
