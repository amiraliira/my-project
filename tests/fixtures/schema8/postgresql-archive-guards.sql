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
 IF NEW.evidence_source='submission-snapshot' AND NOT EXISTS(
  SELECT 1 FROM orders o JOIN participants p ON p.order_id=o.id
  JOIN users u ON u.id=p.person_id
  WHERE o.id=NEW.order_id AND o.workflow_revision=NEW.workflow_revision AND o.status='supervisor'
   AND o.assignee_id=NEW.performer_id AND p.person_id=NEW.performer_id AND p.specialty=NEW.specialty
   AND NEW.submitted_by=NEW.performer_id AND u.role='technician' AND u.active=1
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
