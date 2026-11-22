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
