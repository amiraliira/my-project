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
