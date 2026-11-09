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
  WHERE o.id=NEW.order_id AND o.workflow_revision=NEW.workflow_revision AND o.status='supervisor'
   AND o.assignee_id=NEW.performer_id AND p.person_id=NEW.performer_id AND p.specialty=NEW.specialty
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
    AND actor.active=1 AND actor.role='supervisor' AND actor.specialty=w.specialty
    AND actor.id=COALESCE((SELECT t.to_supervisor_id FROM pm_responsibility_transfers t
      WHERE t.order_id=o.id ORDER BY t.order_version DESC LIMIT 1),w.supervisor_id)
    AND o.assignee_id=NEW.performer_id AND participant.specialty=w.specialty
    AND participant.minutes=o.duration_minutes
    AND base.order_id=o.id AND base.workflow_revision=NEW.workflow_revision-1
    AND base.workflow_revision=CAST(NEW.body::jsonb->'correction'->>'baseRevision' AS INTEGER)
    AND base.performer_id=NEW.performer_id AND base.template_id=NEW.template_id
    AND base.template_version=NEW.template_version AND base.plan_id=NEW.plan_id
    AND base.plan_version=NEW.plan_version AND base.asset_id=NEW.asset_id
    AND base.work_date=NEW.work_date
    AND base.body::jsonb->'results'=NEW.body::jsonb->'results'
    AND base.body::jsonb->'window'=NEW.body::jsonb->'window'
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
