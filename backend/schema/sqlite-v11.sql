-- Schema 11: explicit NEW-database initialization only.
-- Schema 7 generated from a verified consistent SQLite snapshot. No company rows or passwords.
-- Explicit initialization of a NEW target database only; runtime must not migrate live data.

CREATE TABLE assets(id TEXT PRIMARY KEY,code TEXT NOT NULL UNIQUE,name TEXT NOT NULL,hall TEXT NOT NULL,line TEXT NOT NULL,body TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1);

CREATE TABLE legacy_records(id TEXT PRIMARY KEY,body TEXT NOT NULL,imported_at TEXT NOT NULL);

CREATE TABLE parts(id TEXT PRIMARY KEY,code TEXT NOT NULL UNIQUE,name TEXT NOT NULL,specification TEXT NOT NULL,unit TEXT NOT NULL,unit_cost NUMERIC NULL CHECK(unit_cost>=0), version INTEGER NOT NULL DEFAULT 1);

CREATE TABLE pm_templates(id TEXT PRIMARY KEY,title TEXT NOT NULL,specialty TEXT NOT NULL,hall TEXT NOT NULL,period TEXT NOT NULL,version INTEGER NOT NULL CHECK(version>0),mapping_status TEXT NOT NULL,body TEXT NOT NULL);

CREATE TABLE schema_version(version INTEGER NOT NULL);

CREATE TABLE users(id TEXT PRIMARY KEY,code TEXT NOT NULL UNIQUE,name TEXT NOT NULL,role TEXT NOT NULL,specialty TEXT NOT NULL,active INTEGER NOT NULL,password_hash TEXT NOT NULL,session_epoch INTEGER NOT NULL);

CREATE TABLE audit(id TEXT PRIMARY KEY,actor_id TEXT NULL REFERENCES users(id),actor_name TEXT NOT NULL,action TEXT NOT NULL,entity TEXT NOT NULL,entity_id TEXT NOT NULL,details TEXT NOT NULL,created_at TEXT NOT NULL);

CREATE TABLE orders(id TEXT PRIMARY KEY,number TEXT NOT NULL UNIQUE,version INTEGER NOT NULL,workflow_revision INTEGER NOT NULL,type TEXT NOT NULL CHECK(type IN ('PM','EM','PROJECT')),status TEXT NOT NULL,asset_id TEXT NOT NULL REFERENCES assets(id),created_by TEXT NOT NULL REFERENCES users(id),assignee_id TEXT NULL REFERENCES users(id),work_date TEXT NOT NULL,due_date TEXT NULL,body TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL, duration_minutes INTEGER NOT NULL DEFAULT 0, title TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '');

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
CREATE TABLE participant_head_scores(order_id TEXT NOT NULL REFERENCES orders(id),revision INTEGER NOT NULL CHECK(revision>0),person_id TEXT NOT NULL REFERENCES users(id),specialty TEXT NOT NULL,coefficient TEXT NOT NULL CHECK(length(coefficient) BETWEEN 1 AND 31 AND CAST(coefficient AS NUMERIC)>=0 AND CAST(coefficient AS NUMERIC)<=9),actor_id TEXT NOT NULL REFERENCES users(id),approval_id TEXT NOT NULL REFERENCES approvals(id),created_at TEXT NOT NULL,PRIMARY KEY(order_id,revision,person_id));
CREATE INDEX ix_participant_head_scores_approval ON participant_head_scores(approval_id);
CREATE TABLE pm_change_requests(id TEXT PRIMARY KEY,request_key TEXT NOT NULL,requester_id TEXT NOT NULL REFERENCES users(id),operation TEXT NOT NULL CHECK(operation IN ('create-template','edit-template','design-template','retire-template','restore-template','edit-plan')),entity_id TEXT NOT NULL,base_version INTEGER NULL,payload TEXT NOT NULL,payload_hash TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ('pending','approved','rejected')),version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),reviewer_id TEXT NULL REFERENCES users(id),decision_reason TEXT NOT NULL DEFAULT '',result TEXT NULL,created_at TEXT NOT NULL,decided_at TEXT NULL,UNIQUE(requester_id,request_key));
CREATE INDEX ix_pm_changes_status ON pm_change_requests(status,created_at DESC,id DESC);
CREATE INDEX ix_pm_changes_requester ON pm_change_requests(requester_id,created_at DESC,id DESC);

INSERT INTO schema_version(version) VALUES(11);

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

-- Fresh SQLite test-schema extension. The v9 base must widen the head-grade CHECK to 9.
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

-- Offline fixtures / explicit schema-9 migration only. Issued identity remains unchanged.
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
CREATE TRIGGER IF NOT EXISTS guard_pm_responsibility_transfer_insert BEFORE INSERT ON pm_responsibility_transfers
WHEN NOT EXISTS(SELECT 1 FROM orders o JOIN pm_weekly_occurrences w ON w.order_id=o.id
 JOIN users target ON target.id=NEW.to_supervisor_id JOIN users actor ON actor.id=NEW.actor_id
 WHERE o.id=NEW.order_id AND o.type='PM' AND o.status='draft' AND o.workflow_revision=1
 AND o.version=NEW.order_version AND w.executed_at IS NULL AND w.submitted_at IS NULL
 AND actor.active=1 AND actor.role IN ('head','manager')
 AND target.active=1 AND target.role='supervisor' AND target.specialty=w.specialty
 AND NEW.from_supervisor_id=COALESCE((SELECT r.to_supervisor_id FROM pm_responsibility_transfers r WHERE r.order_id=o.id ORDER BY r.order_version DESC LIMIT 1),w.supervisor_id)
 AND o.duration_minutes=0 AND trim(o.description)=''
 AND COALESCE(json_array_length(o.body,'$.participants'),0)=0
 AND COALESCE(json_array_length(o.body,'$.parts'),0)=0
 AND COALESCE(json_array_length(o.body,'$.checklist'),0)=0
 AND COALESCE(json_extract(o.body,'$.description'),'')=''
 AND COALESCE(json_extract(o.body,'$.startTime'),'')=''
 AND COALESCE(json_extract(o.body,'$.endTime'),'')=''
 AND COALESCE(json_extract(o.body,'$.durationMinutes'),0)=0
 AND COALESCE(json_extract(o.body,'$.downtimeMinutes'),0)=0
 AND COALESCE(json_extract(o.body,'$.productionStopped'),0)=0
 AND COALESCE(json_type(o.body,'$.pmResults'),'object')='object'
 AND NOT EXISTS(SELECT 1 FROM json_each(o.body,'$.pmResults') r WHERE r.type<>'text' OR length(trim(CAST(r.value AS TEXT)))>0)
 AND NOT EXISTS(SELECT 1 FROM participants p WHERE p.order_id=o.id)
 AND NOT EXISTS(SELECT 1 FROM order_parts p WHERE p.order_id=o.id)
 AND NOT EXISTS(SELECT 1 FROM approvals a WHERE a.order_id=o.id)
 AND NOT EXISTS(SELECT 1 FROM participant_scores s WHERE s.order_id=o.id)
 AND NOT EXISTS(SELECT 1 FROM participant_head_scores s WHERE s.order_id=o.id)
 AND NOT EXISTS(SELECT 1 FROM participant_grading_policies s WHERE s.order_id=o.id)
 AND NOT EXISTS(SELECT 1 FROM completed_scores s WHERE s.order_id=o.id)
 AND NOT EXISTS(SELECT 1 FROM part_consumptions s WHERE s.order_id=o.id)
 AND NOT EXISTS(SELECT 1 FROM pm_submission_archive a WHERE a.order_id=o.id))
BEGIN SELECT RAISE(ABORT,'PM responsibility transfer requires untouched draft and current active same-specialty supervisor'); END;
CREATE TRIGGER IF NOT EXISTS guard_pm_responsibility_transfers_update BEFORE UPDATE ON pm_responsibility_transfers
BEGIN SELECT RAISE(ABORT,'PM responsibility transfer history is immutable'); END;
CREATE TRIGGER IF NOT EXISTS guard_pm_responsibility_transfers_delete BEFORE DELETE ON pm_responsibility_transfers
BEGIN SELECT RAISE(ABORT,'PM responsibility transfer history is immutable'); END;

-- Offline schema-11 routing guard contract. Explicit fixture/migration only.
DROP TRIGGER IF EXISTS guard_pm_responsibility_transfer_insert;
DROP TRIGGER IF EXISTS guard_pm_archive_insert;
DROP TRIGGER IF EXISTS guard_pm_archive_correction_insert;
CREATE TRIGGER IF NOT EXISTS guard_pm_responsibility_transfer_insert BEFORE INSERT ON pm_responsibility_transfers
WHEN NOT EXISTS(SELECT 1 FROM orders o JOIN pm_weekly_occurrences w ON w.order_id=o.id
 JOIN users target ON target.id=NEW.to_supervisor_id JOIN users actor ON actor.id=NEW.actor_id
 WHERE o.id=NEW.order_id AND o.type='PM' AND o.status='draft' AND o.workflow_revision=1
 AND o.version=NEW.order_version AND w.executed_at IS NULL AND w.submitted_at IS NULL
 AND actor.active=1 AND actor.role IN ('head','manager')
 AND target.active=1 AND target.role='supervisor' AND length(trim(target.specialty))>0
 AND NEW.from_supervisor_id=COALESCE((SELECT r.to_supervisor_id FROM pm_responsibility_transfers r WHERE r.order_id=o.id ORDER BY r.order_version DESC LIMIT 1),w.supervisor_id)
 AND o.duration_minutes=0 AND trim(o.description)=''
 AND COALESCE(json_array_length(o.body,'$.participants'),0)=0
 AND COALESCE(json_array_length(o.body,'$.parts'),0)=0
 AND COALESCE(json_array_length(o.body,'$.checklist'),0)=0
 AND COALESCE(json_extract(o.body,'$.description'),'')=''
 AND COALESCE(json_extract(o.body,'$.startTime'),'')=''
 AND COALESCE(json_extract(o.body,'$.endTime'),'')=''
 AND COALESCE(json_extract(o.body,'$.durationMinutes'),0)=0
 AND COALESCE(json_extract(o.body,'$.downtimeMinutes'),0)=0
 AND COALESCE(json_extract(o.body,'$.productionStopped'),0)=0
 AND COALESCE(json_type(o.body,'$.pmResults'),'object')='object'
 AND NOT EXISTS(SELECT 1 FROM json_each(o.body,'$.pmResults') r WHERE r.type<>'text' OR length(trim(CAST(r.value AS TEXT)))>0)
 AND NOT EXISTS(SELECT 1 FROM participants p WHERE p.order_id=o.id)
 AND NOT EXISTS(SELECT 1 FROM order_parts p WHERE p.order_id=o.id)
 AND NOT EXISTS(SELECT 1 FROM approvals a WHERE a.order_id=o.id)
 AND NOT EXISTS(SELECT 1 FROM participant_scores s WHERE s.order_id=o.id)
 AND NOT EXISTS(SELECT 1 FROM participant_head_scores s WHERE s.order_id=o.id)
 AND NOT EXISTS(SELECT 1 FROM participant_grading_policies s WHERE s.order_id=o.id)
 AND NOT EXISTS(SELECT 1 FROM completed_scores s WHERE s.order_id=o.id)
 AND NOT EXISTS(SELECT 1 FROM part_consumptions s WHERE s.order_id=o.id)
 AND NOT EXISTS(SELECT 1 FROM pm_submission_archive a WHERE a.order_id=o.id))
BEGIN SELECT RAISE(ABORT,'PM responsibility transfer requires untouched draft and current active responsible supervisor'); END;

-- Offline fixtures only. The hosted application uses PostgreSQL exclusively.
CREATE TRIGGER IF NOT EXISTS guard_pm_archive_insert BEFORE INSERT ON pm_submission_archive
WHEN NOT EXISTS(SELECT 1 FROM orders o JOIN pm_weekly_occurrences w ON w.order_id=o.id JOIN pm_weekly_plans p ON p.id=w.plan_id
 WHERE o.id=NEW.order_id AND o.type='PM' AND o.workflow_revision>=NEW.workflow_revision
 AND w.template_id=NEW.template_id AND w.template_version=NEW.template_version
 AND w.plan_id=NEW.plan_id AND w.plan_version=NEW.plan_version AND p.template_id=w.template_id
 AND w.supervisor_id=NEW.supervisor_id AND w.specialty=NEW.specialty AND o.asset_id=NEW.asset_id)
 OR (NEW.evidence_source IN ('submission-snapshot','supervisor-correction-snapshot') AND NOT EXISTS(SELECT 1 FROM orders o JOIN participants p ON p.order_id=o.id JOIN users u ON u.id=p.person_id
 JOIN pm_weekly_occurrences routed ON routed.order_id=o.id
 JOIN users responsible ON responsible.id=COALESCE((SELECT route.to_supervisor_id FROM pm_responsibility_transfers route WHERE route.order_id=o.id ORDER BY route.order_version DESC LIMIT 1),routed.supervisor_id)
 WHERE o.id=NEW.order_id AND o.workflow_revision=NEW.workflow_revision AND o.status='supervisor'
 AND o.assignee_id=NEW.performer_id AND p.person_id=NEW.performer_id AND p.specialty=u.specialty
 AND responsible.active=1 AND responsible.role='supervisor' AND responsible.specialty=p.specialty AND length(trim(p.specialty))>0
 AND json_type(NEW.body,'$.execution.participants')='array' AND json_array_length(NEW.body,'$.execution.participants')=1
 AND json_extract(NEW.body,'$.execution.participants[0].personId')=p.person_id
 AND json_extract(NEW.body,'$.execution.participants[0].specialty')=p.specialty
 AND json_extract(NEW.body,'$.execution.participants[0].minutes')=p.minutes
 AND (NEW.evidence_source='supervisor-correction-snapshot' OR NEW.submitted_by=NEW.performer_id) AND u.role='technician' AND u.active=1
 AND (SELECT COUNT(*) FROM participants n WHERE n.order_id=o.id)=1))
BEGIN SELECT RAISE(ABORT,'PM archive has no matching submitted form'); END;
CREATE TRIGGER IF NOT EXISTS guard_pm_archive_update BEFORE UPDATE ON pm_submission_archive BEGIN SELECT RAISE(ABORT,'PM archive is immutable'); END;
CREATE TRIGGER IF NOT EXISTS guard_pm_archive_delete BEFORE DELETE ON pm_submission_archive BEGIN SELECT RAISE(ABORT,'PM archive is immutable'); END;
CREATE TRIGGER IF NOT EXISTS guard_pm_archive_correction_insert BEFORE INSERT ON pm_submission_archive
WHEN NEW.evidence_source='supervisor-correction-snapshot' AND (
 json_extract(NEW.body,'$.provenance') IS NOT 'supervisor-correction-snapshot'
 OR json_type(NEW.body,'$.correction') IS NOT 'object'
 OR json_type(NEW.body,'$.correction.baseRevision') IS NOT 'integer'
 OR length(trim(COALESCE(json_extract(NEW.body,'$.correction.reason'),''))) NOT BETWEEN 3 AND 2000
 OR NOT EXISTS(SELECT 1 FROM orders o JOIN pm_weekly_occurrences w ON w.order_id=o.id
  JOIN users actor ON actor.id=NEW.submitted_by
  JOIN pm_submission_archive base ON base.id=json_extract(NEW.body,'$.correction.baseArchiveId')
  JOIN participants participant ON participant.order_id=o.id AND participant.person_id=NEW.performer_id
  WHERE o.id=NEW.order_id AND o.status='supervisor' AND o.workflow_revision=NEW.workflow_revision
   AND actor.active=1 AND actor.role='supervisor' AND actor.specialty=participant.specialty
   AND actor.id=COALESCE((SELECT t.to_supervisor_id FROM pm_responsibility_transfers t WHERE t.order_id=o.id ORDER BY t.order_version DESC LIMIT 1),w.supervisor_id)
   AND o.assignee_id=NEW.performer_id AND participant.specialty=actor.specialty AND length(trim(participant.specialty))>0 AND participant.minutes=o.duration_minutes
   AND base.order_id=o.id AND base.workflow_revision=NEW.workflow_revision-1
   AND base.workflow_revision=json_extract(NEW.body,'$.correction.baseRevision') AND base.performer_id=NEW.performer_id
   AND base.template_id=NEW.template_id AND base.template_version=NEW.template_version AND base.plan_id=NEW.plan_id
   AND base.plan_version=NEW.plan_version AND base.asset_id=NEW.asset_id AND base.work_date=NEW.work_date
   AND json_extract(base.body,'$.results')=json_extract(NEW.body,'$.results')
   AND json_extract(base.body,'$.window') IS json_extract(NEW.body,'$.window')
   AND json_extract(base.body,'$.responsibility') IS json_extract(NEW.body,'$.responsibility')
   AND json_extract(base.body,'$.equipmentMembers') IS json_extract(NEW.body,'$.equipmentMembers')
   AND json_extract(NEW.body,'$.execution.description')=o.description
   AND NEW.work_date=o.work_date
   AND json_extract(NEW.body,'$.execution.workDate')=o.work_date
   AND json_extract(NEW.body,'$.execution.startTime')=json_extract(o.body,'$.startTime')
   AND json_extract(NEW.body,'$.execution.endTime')=json_extract(o.body,'$.endTime')
   AND json_extract(NEW.body,'$.execution.durationMinutes')=o.duration_minutes
   AND json_extract(NEW.body,'$.results')=json_extract(o.body,'$.pmResults')
   AND w.executed_at=NEW.executed_at AND w.submitted_at=NEW.submitted_at
   AND json_type(NEW.body,'$.execution.parts')='array'
   AND json_array_length(NEW.body,'$.execution.parts')=(SELECT COUNT(*) FROM order_parts op WHERE op.order_id=o.id)
   AND (SELECT COUNT(DISTINCT json_extract(q.value,'$.partId')) FROM json_each(NEW.body,'$.execution.parts') q)=json_array_length(NEW.body,'$.execution.parts')
   AND NOT EXISTS(SELECT 1 FROM json_each(NEW.body,'$.execution.parts') q
     WHERE json_type(q.value,'$.quantity') NOT IN ('integer','real') OR NOT EXISTS(
      SELECT 1 FROM order_parts op JOIN parts master ON master.id=op.part_id
      WHERE op.order_id=o.id AND op.part_id=json_extract(q.value,'$.partId')
       AND pak_archive_quantity_units(q.value->'$.quantity')=op.quantity_units
       AND json_extract(q.value,'$.code')=master.code AND json_extract(q.value,'$.name')=master.name AND json_extract(q.value,'$.unit')=master.unit))
   AND json_array_length(NEW.body,'$.execution.participants')=1
   AND json_extract(NEW.body,'$.execution.participants[0].personId')=NEW.performer_id
   AND json_extract(NEW.body,'$.execution.participants[0].minutes')=participant.minutes
   AND EXISTS(SELECT 1 FROM audit event WHERE event.entity='order' AND event.entity_id=o.id
    AND event.action='review-correction' AND event.actor_id=NEW.submitted_by AND event.created_at=NEW.submitted_at
    AND json_extract(event.details,'$.fromRevision')=json_extract(NEW.body,'$.correction.baseRevision')
    AND json_extract(event.details,'$.toRevision')=NEW.workflow_revision
    AND json_extract(event.details,'$.reason')=json_extract(NEW.body,'$.correction.reason'))
   AND NOT EXISTS(SELECT 1 FROM approvals a WHERE a.order_id=o.id AND a.revision=o.workflow_revision)))
BEGIN SELECT RAISE(ABORT,'PM supervisor correction must preserve its immutable base and match authorized review'); END;

CREATE INDEX IF NOT EXISTS ix_pm_weekly_plan_owner_template ON pm_weekly_plans(supervisor_id,template_id);
CREATE INDEX IF NOT EXISTS ix_pm_weekly_template_order ON pm_weekly_occurrences(template_id,order_id);
