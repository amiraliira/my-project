-- Offline fixtures only. The hosted application uses PostgreSQL exclusively.
CREATE TRIGGER IF NOT EXISTS guard_pm_archive_insert BEFORE INSERT ON pm_submission_archive
WHEN NOT EXISTS(SELECT 1 FROM orders o JOIN pm_weekly_occurrences w ON w.order_id=o.id JOIN pm_weekly_plans p ON p.id=w.plan_id
 WHERE o.id=NEW.order_id AND o.type='PM' AND o.workflow_revision>=NEW.workflow_revision
 AND w.template_id=NEW.template_id AND w.template_version=NEW.template_version
 AND w.plan_id=NEW.plan_id AND w.plan_version=NEW.plan_version AND p.template_id=w.template_id
 AND w.supervisor_id=NEW.supervisor_id AND w.specialty=NEW.specialty AND o.asset_id=NEW.asset_id)
 OR (NEW.evidence_source='submission-snapshot' AND NOT EXISTS(SELECT 1 FROM orders o JOIN participants p ON p.order_id=o.id JOIN users u ON u.id=p.person_id
 WHERE o.id=NEW.order_id AND o.workflow_revision=NEW.workflow_revision AND o.status='supervisor'
 AND o.assignee_id=NEW.performer_id AND p.person_id=NEW.performer_id AND p.specialty=NEW.specialty
 AND NEW.submitted_by=NEW.performer_id AND u.role='technician' AND u.active=1
 AND (SELECT COUNT(*) FROM participants n WHERE n.order_id=o.id)=1))
BEGIN SELECT RAISE(ABORT,'PM archive has no matching submitted form'); END;
CREATE TRIGGER IF NOT EXISTS guard_pm_archive_update BEFORE UPDATE ON pm_submission_archive BEGIN SELECT RAISE(ABORT,'PM archive is immutable'); END;
CREATE TRIGGER IF NOT EXISTS guard_pm_archive_delete BEFORE DELETE ON pm_submission_archive BEGIN SELECT RAISE(ABORT,'PM archive is immutable'); END;
