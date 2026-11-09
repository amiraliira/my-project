-- Explicit initialization/migration owner only. No company rows or runtime DDL.
-- Historical NULL grades remain unknown; they are never replaced with a score.
CREATE TRIGGER IF NOT EXISTS guard_participant_grading_policy_insert BEFORE INSERT ON participant_grading_policies
WHEN NOT EXISTS(SELECT 1 FROM orders o JOIN participants p ON p.order_id=o.id JOIN users u ON u.id=p.person_id
 WHERE o.id=NEW.order_id AND o.workflow_revision=NEW.revision AND o.status='supervisor'
 AND p.person_id=NEW.person_id AND p.specialty=NEW.specialty
 AND u.active=1 AND u.specialty=NEW.specialty AND u.role=NEW.role_at_submission
 AND NEW.policy=CASE WHEN u.role='supervisor' THEN 'head-only-v1' ELSE 'two-stage-v1' END)
BEGIN SELECT RAISE(ABORT,'invalid submitted participant grading policy'); END;
CREATE TRIGGER IF NOT EXISTS guard_participant_grading_policies_update BEFORE UPDATE ON participant_grading_policies BEGIN SELECT RAISE(ABORT,'submitted grading policy is immutable'); END;
CREATE TRIGGER IF NOT EXISTS guard_participant_grading_policies_delete BEFORE DELETE ON participant_grading_policies BEGIN SELECT RAISE(ABORT,'submitted grading policy is immutable'); END;
CREATE TRIGGER IF NOT EXISTS guard_approval_evidence_insert BEFORE INSERT ON approvals
WHEN NEW.revision<=0 OR NEW.stage NOT IN ('supervisor','head') OR NEW.decision NOT IN ('approve','return')
 OR (NEW.coefficient IS NOT NULL AND (NEW.coefficient<0 OR NEW.coefficient>3))
 OR NOT EXISTS(SELECT 1 FROM orders o WHERE o.id=NEW.order_id AND NEW.revision<=o.workflow_revision)
BEGIN SELECT RAISE(ABORT,'invalid approval evidence'); END;
CREATE TRIGGER IF NOT EXISTS guard_participant_score_evidence_insert BEFORE INSERT ON participant_scores
WHEN NEW.actor_id=NEW.person_id OR EXISTS(SELECT 1 FROM participant_grading_policies gp WHERE gp.order_id=NEW.order_id AND gp.revision=NEW.revision AND gp.person_id=NEW.person_id AND gp.policy='head-only-v1') OR NOT EXISTS(SELECT 1 FROM approvals a JOIN orders o ON o.id=a.order_id
 WHERE a.id=NEW.approval_id AND a.order_id=NEW.order_id AND a.revision=NEW.revision
 AND a.stage='supervisor' AND a.decision='approve' AND a.specialty=NEW.specialty
 AND a.actor_id=NEW.actor_id AND (a.revision<o.workflow_revision OR EXISTS(SELECT 1 FROM participants p
 WHERE p.order_id=a.order_id AND p.person_id=NEW.person_id AND p.specialty=NEW.specialty)))
BEGIN SELECT RAISE(ABORT,'participant score has no matching supervisor approval'); END;
CREATE TRIGGER IF NOT EXISTS guard_consumption_evidence_insert BEFORE INSERT ON part_consumptions
WHEN NOT EXISTS(SELECT 1 FROM orders o JOIN approvals a ON a.order_id=o.id
 JOIN order_parts line ON line.order_id=o.id AND line.part_id=NEW.part_id
 WHERE o.id=NEW.order_id AND o.status='completed' AND o.workflow_revision=NEW.revision
 AND a.id=NEW.approval_id AND a.revision=NEW.revision AND a.stage='head' AND a.decision='approve'
 AND a.actor_id=NEW.actor_id AND line.quantity_units=NEW.quantity_units)
BEGIN SELECT RAISE(ABORT,'consumption has no matching final approval'); END;
CREATE TRIGGER IF NOT EXISTS guard_completed_score_evidence_insert BEFORE INSERT ON completed_scores
WHEN NEW.revision<=0 OR (NEW.source IS NOT NULL AND NEW.source NOT IN ('individual','legacy-specialty','head-only'))
 OR (NEW.supervisor_coefficient IS NOT NULL AND (NEW.supervisor_coefficient<0 OR NEW.supervisor_coefficient>3))
 OR (NEW.head_coefficient IS NOT NULL AND (NEW.head_coefficient<0 OR NEW.head_coefficient>CASE WHEN NEW.source='head-only' THEN 9 ELSE 3 END))
 OR (NEW.score_cents IS NOT NULL AND (typeof(NEW.score_cents)<>'integer' OR NEW.score_cents<0))
 OR (NEW.source='head-only' AND (NEW.supervisor_coefficient IS NOT NULL OR NEW.head_coefficient IS NULL OR NEW.head_coefficient_exact IS NULL OR NEW.score_raw IS NULL OR NEW.score_cents IS NULL OR NOT EXISTS(SELECT 1 FROM participant_grading_policies gp WHERE gp.order_id=NEW.order_id AND gp.revision=NEW.revision AND gp.person_id=NEW.person_id AND gp.policy='head-only-v1')))
 OR (COALESCE(NEW.source,'')<>'head-only' AND EXISTS(SELECT 1 FROM participant_grading_policies gp WHERE gp.order_id=NEW.order_id AND gp.revision=NEW.revision AND gp.person_id=NEW.person_id AND gp.policy='head-only-v1'))
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
BEGIN SELECT RAISE(ABORT,'invalid completed score evidence'); END;
CREATE TRIGGER IF NOT EXISTS guard_approvals_update BEFORE UPDATE ON approvals BEGIN SELECT RAISE(ABORT,'approval history is immutable'); END;
CREATE TRIGGER IF NOT EXISTS guard_approvals_delete BEFORE DELETE ON approvals BEGIN SELECT RAISE(ABORT,'approval history is immutable'); END;
CREATE TRIGGER IF NOT EXISTS guard_participant_scores_update BEFORE UPDATE ON participant_scores BEGIN SELECT RAISE(ABORT,'participant score history is immutable'); END;
CREATE TRIGGER IF NOT EXISTS guard_participant_scores_delete BEFORE DELETE ON participant_scores BEGIN SELECT RAISE(ABORT,'participant score history is immutable'); END;
CREATE TRIGGER IF NOT EXISTS guard_request_claims_update BEFORE UPDATE ON request_claims BEGIN SELECT RAISE(ABORT,'request claim is immutable'); END;
CREATE TRIGGER IF NOT EXISTS guard_request_claims_delete BEFORE DELETE ON request_claims BEGIN SELECT RAISE(ABORT,'request claim is immutable'); END;
CREATE TRIGGER IF NOT EXISTS guard_audit_update BEFORE UPDATE ON audit BEGIN SELECT RAISE(ABORT,'audit history is immutable'); END;
CREATE TRIGGER IF NOT EXISTS guard_audit_delete BEFORE DELETE ON audit BEGIN SELECT RAISE(ABORT,'audit history is immutable'); END;

CREATE TRIGGER IF NOT EXISTS guard_participant_head_score_evidence_insert BEFORE INSERT ON participant_head_scores
WHEN NEW.actor_id=NEW.person_id OR typeof(NEW.coefficient)<>'text' OR NOT (NEW.coefficient IN ('0','1','2','3','4','5','6','7','8','9') OR
 (length(NEW.coefficient) BETWEEN 3 AND 30 AND substr(NEW.coefficient,1,2) IN ('0.','1.','2.','3.','4.','5.','6.','7.','8.','9.')
 AND substr(NEW.coefficient,3) NOT GLOB '*[^0-9]*'))
 OR CAST(NEW.coefficient AS NUMERIC)<0 OR CAST(NEW.coefficient AS NUMERIC)>CASE WHEN EXISTS(SELECT 1 FROM participant_grading_policies gp WHERE gp.order_id=NEW.order_id AND gp.revision=NEW.revision AND gp.person_id=NEW.person_id AND gp.policy='head-only-v1') THEN 9 ELSE 3 END
 OR (substr(NEW.coefficient,1,2)=CASE WHEN EXISTS(SELECT 1 FROM participant_grading_policies gp WHERE gp.order_id=NEW.order_id AND gp.revision=NEW.revision AND gp.person_id=NEW.person_id AND gp.policy='head-only-v1') THEN '9.' ELSE '3.' END AND substr(NEW.coefficient,3) GLOB '*[1-9]*')
 OR NOT EXISTS(SELECT 1 FROM approvals a JOIN orders o ON o.id=a.order_id
 JOIN participants p ON p.order_id=o.id AND p.person_id=NEW.person_id AND p.specialty=NEW.specialty
 JOIN users u ON u.id=a.actor_id
 WHERE a.id=NEW.approval_id AND a.order_id=NEW.order_id AND a.revision=NEW.revision
 AND o.workflow_revision=NEW.revision AND o.status='head'
 AND a.stage='head' AND a.decision='approve' AND a.coefficient IS NULL
 AND a.actor_id=NEW.actor_id AND u.role='head' AND u.active=1)
BEGIN SELECT RAISE(ABORT,'participant head score has no matching individual chief approval'); END;
CREATE TRIGGER IF NOT EXISTS guard_participant_head_scores_update BEFORE UPDATE ON participant_head_scores BEGIN SELECT RAISE(ABORT,'individual chief score history is immutable'); END;
CREATE TRIGGER IF NOT EXISTS guard_participant_head_scores_delete BEFORE DELETE ON participant_head_scores BEGIN SELECT RAISE(ABORT,'individual chief score history is immutable'); END;

CREATE TRIGGER IF NOT EXISTS guard_pm_change_request_insert BEFORE INSERT ON pm_change_requests
WHEN NEW.status<>'pending' OR NEW.version<>1 OR NEW.reviewer_id IS NOT NULL OR NEW.decided_at IS NOT NULL OR NEW.result IS NOT NULL
 OR NOT EXISTS(SELECT 1 FROM users u WHERE u.id=NEW.requester_id AND u.active=1 AND u.role IN ('head','manager','planner','supervisor','admin'))
BEGIN SELECT RAISE(ABORT,'new PM change request must start pending'); END;
CREATE TRIGGER IF NOT EXISTS guard_pm_change_request_update BEFORE UPDATE ON pm_change_requests
WHEN OLD.status<>'pending' OR NEW.status NOT IN ('approved','rejected') OR NEW.version<>OLD.version+1
 OR NEW.id IS NOT OLD.id OR NEW.request_key IS NOT OLD.request_key OR NEW.requester_id IS NOT OLD.requester_id
 OR NEW.operation IS NOT OLD.operation OR NEW.entity_id IS NOT OLD.entity_id OR NEW.base_version IS NOT OLD.base_version
 OR NEW.payload IS NOT OLD.payload OR NEW.payload_hash IS NOT OLD.payload_hash OR NEW.created_at IS NOT OLD.created_at
 OR NEW.decided_at IS NULL OR NOT EXISTS(SELECT 1 FROM users u WHERE u.id=NEW.reviewer_id AND u.active=1 AND u.role='head')
BEGIN SELECT RAISE(ABORT,'PM change decision must be immutable and approved by a chief'); END;
CREATE TRIGGER IF NOT EXISTS guard_pm_change_request_delete BEFORE DELETE ON pm_change_requests BEGIN SELECT RAISE(ABORT,'PM change review history is immutable'); END;
