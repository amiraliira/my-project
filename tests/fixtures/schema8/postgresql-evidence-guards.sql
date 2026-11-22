-- Explicit initialization/migration owner only. No company rows or runtime DDL.
CREATE OR REPLACE FUNCTION pak_guard_insert_evidence() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_TABLE_NAME='approvals' THEN
  IF NEW.revision<=0 OR NEW.stage NOT IN ('supervisor','head') OR NEW.decision NOT IN ('approve','return')
   OR (NEW.coefficient IS NOT NULL AND (NEW.coefficient<0 OR NEW.coefficient>3))
   OR NOT EXISTS(SELECT 1 FROM orders o WHERE o.id=NEW.order_id AND NEW.revision<=o.workflow_revision)
  THEN RAISE EXCEPTION 'invalid approval evidence' USING ERRCODE='23514'; END IF;
 ELSIF TG_TABLE_NAME='participant_scores' THEN
  IF NOT EXISTS(SELECT 1 FROM approvals a JOIN orders o ON o.id=a.order_id
   WHERE a.id=NEW.approval_id AND a.order_id=NEW.order_id AND a.revision=NEW.revision
   AND a.stage='supervisor' AND a.decision='approve' AND a.specialty=NEW.specialty
   AND a.actor_id=NEW.actor_id AND (a.revision<o.workflow_revision OR EXISTS(SELECT 1 FROM participants p
    WHERE p.order_id=a.order_id AND p.person_id=NEW.person_id AND p.specialty=NEW.specialty)))
  THEN RAISE EXCEPTION 'participant score has no matching supervisor approval' USING ERRCODE='23514'; END IF;
 ELSIF TG_TABLE_NAME='participant_head_scores' THEN
  IF NEW.coefficient !~ '^[0-3](\.[0-9]{1,28})?$' OR CAST(NEW.coefficient AS NUMERIC)<0 OR CAST(NEW.coefficient AS NUMERIC)>3
   OR NOT EXISTS(SELECT 1 FROM approvals a JOIN orders o ON o.id=a.order_id
    JOIN participants p ON p.order_id=o.id AND p.person_id=NEW.person_id AND p.specialty=NEW.specialty
    JOIN users u ON u.id=a.actor_id
    WHERE a.id=NEW.approval_id AND a.order_id=NEW.order_id AND a.revision=NEW.revision
    AND o.workflow_revision=NEW.revision AND o.status='head'
    AND a.stage='head' AND a.decision='approve' AND a.coefficient IS NULL
    AND a.actor_id=NEW.actor_id AND u.role='head' AND u.active=1)
  THEN RAISE EXCEPTION 'participant head score has no matching individual chief approval' USING ERRCODE='23514'; END IF;
 ELSIF TG_TABLE_NAME='part_consumptions' THEN
  IF NOT EXISTS(SELECT 1 FROM orders o JOIN approvals a ON a.order_id=o.id
   JOIN order_parts line ON line.order_id=o.id AND line.part_id=NEW.part_id
   WHERE o.id=NEW.order_id AND o.status='completed' AND o.workflow_revision=NEW.revision
   AND a.id=NEW.approval_id AND a.revision=NEW.revision AND a.stage='head' AND a.decision='approve'
   AND a.actor_id=NEW.actor_id AND line.quantity_units=NEW.quantity_units)
  THEN RAISE EXCEPTION 'consumption has no matching final approval' USING ERRCODE='23514'; END IF;
 ELSIF TG_TABLE_NAME='completed_scores' THEN
  IF NEW.revision<=0 OR (NEW.source IS NOT NULL AND NEW.source NOT IN ('individual','legacy-specialty'))
   OR (NEW.supervisor_coefficient IS NOT NULL AND (NEW.supervisor_coefficient<0 OR NEW.supervisor_coefficient>3))
   OR (NEW.head_coefficient IS NOT NULL AND (NEW.head_coefficient<0 OR NEW.head_coefficient>3))
   OR (NEW.score_cents IS NOT NULL AND NEW.score_cents<0)
   OR (NEW.head_coefficient_exact IS NOT NULL AND NOT EXISTS(SELECT 1 FROM participant_head_scores h
    WHERE h.order_id=NEW.order_id AND h.revision=NEW.revision AND h.person_id=NEW.person_id
    AND h.coefficient=NEW.head_coefficient_exact AND CAST(h.coefficient AS NUMERIC)=NEW.head_coefficient))
   OR (NEW.head_coefficient_exact IS NULL AND EXISTS(SELECT 1 FROM participant_head_scores h
    WHERE h.order_id=NEW.order_id AND h.revision=NEW.revision AND h.person_id=NEW.person_id))
   OR (NEW.head_coefficient_exact IS NULL AND NEW.head_coefficient IS NOT NULL AND NOT EXISTS(SELECT 1 FROM approvals a
    WHERE a.order_id=NEW.order_id AND a.revision=NEW.revision AND a.stage='head' AND a.decision='approve' AND a.coefficient=NEW.head_coefficient))
   OR NOT EXISTS(SELECT 1 FROM orders o JOIN participants p ON p.order_id=o.id
    WHERE o.id=NEW.order_id AND o.status='completed' AND o.workflow_revision=NEW.revision AND p.person_id=NEW.person_id)
   OR (NEW.source='individual' AND NOT EXISTS(SELECT 1 FROM participant_scores s
    WHERE s.order_id=NEW.order_id AND s.revision=NEW.revision AND s.person_id=NEW.person_id AND s.coefficient=NEW.supervisor_coefficient))
   OR (NEW.source='legacy-specialty' AND NOT EXISTS(SELECT 1 FROM approvals a JOIN participants p ON p.order_id=a.order_id
    WHERE a.order_id=NEW.order_id AND a.revision=NEW.revision AND a.stage='supervisor' AND a.decision='approve'
    AND a.specialty=p.specialty AND p.person_id=NEW.person_id AND a.coefficient=NEW.supervisor_coefficient))
  THEN RAISE EXCEPTION 'invalid completed score evidence' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END; $$;
DROP TRIGGER IF EXISTS guard_approval_evidence_insert ON approvals;
CREATE TRIGGER guard_approval_evidence_insert BEFORE INSERT ON approvals FOR EACH ROW EXECUTE FUNCTION pak_guard_insert_evidence();
DROP TRIGGER IF EXISTS guard_participant_score_evidence_insert ON participant_scores;
CREATE TRIGGER guard_participant_score_evidence_insert BEFORE INSERT ON participant_scores FOR EACH ROW EXECUTE FUNCTION pak_guard_insert_evidence();
DROP TRIGGER IF EXISTS guard_participant_head_score_evidence_insert ON participant_head_scores;
CREATE TRIGGER guard_participant_head_score_evidence_insert BEFORE INSERT ON participant_head_scores FOR EACH ROW EXECUTE FUNCTION pak_guard_insert_evidence();
DROP TRIGGER IF EXISTS guard_consumption_evidence_insert ON part_consumptions;
CREATE TRIGGER guard_consumption_evidence_insert BEFORE INSERT ON part_consumptions FOR EACH ROW EXECUTE FUNCTION pak_guard_insert_evidence();
DROP TRIGGER IF EXISTS guard_completed_score_evidence_insert ON completed_scores;
CREATE TRIGGER guard_completed_score_evidence_insert BEFORE INSERT ON completed_scores FOR EACH ROW EXECUTE FUNCTION pak_guard_insert_evidence();
CREATE OR REPLACE FUNCTION pak_reject_evidence_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'published evidence is immutable' USING ERRCODE='23514'; END; $$;
DROP TRIGGER IF EXISTS guard_approvals_history ON approvals;
CREATE TRIGGER guard_approvals_history BEFORE UPDATE OR DELETE ON approvals FOR EACH ROW EXECUTE FUNCTION pak_reject_evidence_mutation();
DROP TRIGGER IF EXISTS guard_participant_scores_history ON participant_scores;
CREATE TRIGGER guard_participant_scores_history BEFORE UPDATE OR DELETE ON participant_scores FOR EACH ROW EXECUTE FUNCTION pak_reject_evidence_mutation();
DROP TRIGGER IF EXISTS guard_participant_head_scores_history ON participant_head_scores;
CREATE TRIGGER guard_participant_head_scores_history BEFORE UPDATE OR DELETE ON participant_head_scores FOR EACH ROW EXECUTE FUNCTION pak_reject_evidence_mutation();
DROP TRIGGER IF EXISTS guard_request_claims_history ON request_claims;
CREATE TRIGGER guard_request_claims_history BEFORE UPDATE OR DELETE ON request_claims FOR EACH ROW EXECUTE FUNCTION pak_reject_evidence_mutation();
DROP TRIGGER IF EXISTS guard_audit_history ON audit;
CREATE TRIGGER guard_audit_history BEFORE UPDATE OR DELETE ON audit FOR EACH ROW EXECUTE FUNCTION pak_reject_evidence_mutation();

CREATE OR REPLACE FUNCTION pak_guard_pm_change_request() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'PM change review history is immutable' USING ERRCODE='23514'; END IF;
 IF TG_OP='INSERT' THEN
  IF NEW.status<>'pending' OR NEW.version<>1 OR NEW.reviewer_id IS NOT NULL OR NEW.decided_at IS NOT NULL OR NEW.result IS NOT NULL
   OR NOT EXISTS(SELECT 1 FROM users u WHERE u.id=NEW.requester_id AND u.active=1 AND u.role IN ('head','manager','planner','supervisor','admin'))
  THEN RAISE EXCEPTION 'new PM change request must start pending' USING ERRCODE='23514'; END IF;
 ELSE
  IF OLD.status<>'pending' OR NEW.status NOT IN ('approved','rejected') OR NEW.version<>OLD.version+1
   OR ROW(NEW.id,NEW.request_key,NEW.requester_id,NEW.operation,NEW.entity_id,NEW.base_version,NEW.payload,NEW.payload_hash,NEW.created_at)
      IS DISTINCT FROM ROW(OLD.id,OLD.request_key,OLD.requester_id,OLD.operation,OLD.entity_id,OLD.base_version,OLD.payload,OLD.payload_hash,OLD.created_at)
   OR NEW.decided_at IS NULL OR NOT EXISTS(SELECT 1 FROM users u WHERE u.id=NEW.reviewer_id AND u.active=1 AND u.role='head')
  THEN RAISE EXCEPTION 'PM change decision must be immutable and approved by a chief' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END; $$;
DROP TRIGGER IF EXISTS guard_pm_change_request_history ON pm_change_requests;
CREATE TRIGGER guard_pm_change_request_history BEFORE INSERT OR UPDATE OR DELETE ON pm_change_requests FOR EACH ROW EXECUTE FUNCTION pak_guard_pm_change_request();
