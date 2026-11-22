-- Published history is append-only. Explicit NEW-database initialization only.
CREATE TRIGGER IF NOT EXISTS pak_pm_template_revision_no_update BEFORE UPDATE ON pm_template_revisions BEGIN SELECT RAISE(ABORT,'Published PM template revision is immutable'); END;
CREATE TRIGGER IF NOT EXISTS pak_pm_template_revision_no_delete BEFORE DELETE ON pm_template_revisions BEGIN SELECT RAISE(ABORT,'Published PM template revision is immutable'); END;
CREATE TRIGGER IF NOT EXISTS pak_pm_plan_revision_no_update BEFORE UPDATE ON pm_plan_revisions BEGIN SELECT RAISE(ABORT,'Published PM plan revision is immutable'); END;
CREATE TRIGGER IF NOT EXISTS pak_pm_plan_revision_no_delete BEFORE DELETE ON pm_plan_revisions BEGIN SELECT RAISE(ABORT,'Published PM plan revision is immutable'); END;
CREATE TRIGGER IF NOT EXISTS pak_pm_occurrence_no_delete BEFORE DELETE ON pm_weekly_occurrences BEGIN SELECT RAISE(ABORT,'Issued PM occurrence is immutable'); END;
CREATE TRIGGER IF NOT EXISTS pak_pm_occurrence_identity_no_update BEFORE UPDATE ON pm_weekly_occurrences
WHEN NEW.plan_id IS NOT OLD.plan_id OR NEW.persian_year IS NOT OLD.persian_year OR NEW.month IS NOT OLD.month OR NEW.week IS NOT OLD.week OR NEW.order_id IS NOT OLD.order_id OR NEW.plan_version IS NOT OLD.plan_version OR NEW.template_id IS NOT OLD.template_id OR NEW.template_version IS NOT OLD.template_version OR NEW.start_utc IS NOT OLD.start_utc OR NEW.end_utc IS NOT OLD.end_utc OR NEW.specialty IS NOT OLD.specialty OR NEW.supervisor_id IS NOT OLD.supervisor_id
BEGIN SELECT RAISE(ABORT,'Issued PM identity, ownership and deadlines are immutable'); END;
CREATE TRIGGER IF NOT EXISTS pak_pm_members_no_update BEFORE UPDATE ON pm_weekly_assets BEGIN SELECT RAISE(ABORT,'Issued PM membership is immutable'); END;
CREATE TRIGGER IF NOT EXISTS pak_pm_members_no_delete BEFORE DELETE ON pm_weekly_assets BEGIN SELECT RAISE(ABORT,'Issued PM membership is immutable'); END;
CREATE TRIGGER IF NOT EXISTS pak_pm_setup_no_update BEFORE UPDATE ON pm_setup_requests BEGIN SELECT RAISE(ABORT,'PM setup request claim is immutable'); END;
CREATE TRIGGER IF NOT EXISTS pak_pm_setup_no_delete BEFORE DELETE ON pm_setup_requests BEGIN SELECT RAISE(ABORT,'PM setup request claim is immutable'); END;
