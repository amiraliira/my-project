-- Explicit owner-run schema 8 -> 9 extension; never run by the application.
-- Historical evidence is retained. Per-policy guards enforce head ranges 3/9.
ALTER TABLE participant_head_scores DROP CONSTRAINT participant_head_scores_coefficient_check;
ALTER TABLE participant_head_scores ADD CONSTRAINT participant_head_scores_coefficient_check
 CHECK(length(coefficient) BETWEEN 1 AND 31 AND CAST(coefficient AS NUMERIC)>=0 AND CAST(coefficient AS NUMERIC)<=9);
CREATE TABLE participant_grading_policies(
 order_id TEXT NOT NULL REFERENCES orders(id),
 revision INTEGER NOT NULL CHECK(revision>0),
 person_id TEXT NOT NULL REFERENCES users(id),
 specialty TEXT NOT NULL,
 role_at_submission TEXT NOT NULL CHECK(role_at_submission IN ('technician','supervisor','planner','head','manager','admin')),
 policy TEXT NOT NULL CHECK(policy IN ('two-stage-v1','head-only-v1')),
 created_at TEXT NOT NULL,
 PRIMARY KEY(order_id,revision,person_id),
 CHECK((role_at_submission='supervisor' AND policy='head-only-v1') OR (role_at_submission<>'supervisor' AND policy='two-stage-v1'))
);
