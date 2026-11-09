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
