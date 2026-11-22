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
