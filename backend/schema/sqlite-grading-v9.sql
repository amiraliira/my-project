-- Fresh SQLite test-schema extension. The v9 base must widen the head-grade CHECK to 9.
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
