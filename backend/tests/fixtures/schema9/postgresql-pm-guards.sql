-- Apply with the separate migration owner, never the runtime role.
CREATE OR REPLACE FUNCTION pak_pm_reject_history_mutation() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Published PM history is immutable' USING ERRCODE='23514'; END; $$;
CREATE OR REPLACE FUNCTION pak_pm_guard_occurrence_identity() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF ROW(NEW.plan_id,NEW.persian_year,NEW.month,NEW.week,NEW.order_id,NEW.plan_version,NEW.template_id,NEW.template_version,NEW.start_utc,NEW.end_utc,NEW.specialty,NEW.supervisor_id)
 IS DISTINCT FROM ROW(OLD.plan_id,OLD.persian_year,OLD.month,OLD.week,OLD.order_id,OLD.plan_version,OLD.template_id,OLD.template_version,OLD.start_utc,OLD.end_utc,OLD.specialty,OLD.supervisor_id)
 THEN RAISE EXCEPTION 'Issued PM identity, ownership and deadlines are immutable' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END; $$;
DROP TRIGGER IF EXISTS pak_pm_template_revision_immutable ON pm_template_revisions;
CREATE TRIGGER pak_pm_template_revision_immutable BEFORE UPDATE OR DELETE ON pm_template_revisions FOR EACH ROW EXECUTE FUNCTION pak_pm_reject_history_mutation();
DROP TRIGGER IF EXISTS pak_pm_plan_revision_immutable ON pm_plan_revisions;
CREATE TRIGGER pak_pm_plan_revision_immutable BEFORE UPDATE OR DELETE ON pm_plan_revisions FOR EACH ROW EXECUTE FUNCTION pak_pm_reject_history_mutation();
DROP TRIGGER IF EXISTS pak_pm_occurrence_no_delete ON pm_weekly_occurrences;
CREATE TRIGGER pak_pm_occurrence_no_delete BEFORE DELETE ON pm_weekly_occurrences FOR EACH ROW EXECUTE FUNCTION pak_pm_reject_history_mutation();
DROP TRIGGER IF EXISTS pak_pm_occurrence_identity_guard ON pm_weekly_occurrences;
CREATE TRIGGER pak_pm_occurrence_identity_guard BEFORE UPDATE ON pm_weekly_occurrences FOR EACH ROW EXECUTE FUNCTION pak_pm_guard_occurrence_identity();
DROP TRIGGER IF EXISTS pak_pm_members_immutable ON pm_weekly_assets;
CREATE TRIGGER pak_pm_members_immutable BEFORE UPDATE OR DELETE ON pm_weekly_assets FOR EACH ROW EXECUTE FUNCTION pak_pm_reject_history_mutation();
DROP TRIGGER IF EXISTS pak_pm_setup_immutable ON pm_setup_requests;
CREATE TRIGGER pak_pm_setup_immutable BEFORE UPDATE OR DELETE ON pm_setup_requests FOR EACH ROW EXECUTE FUNCTION pak_pm_reject_history_mutation();
