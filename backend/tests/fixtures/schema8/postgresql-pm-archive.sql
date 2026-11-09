-- Additive schema-7 to schema-8 objects. Explicit migration owner only.
CREATE TABLE IF NOT EXISTS pm_submission_archive(
 id TEXT PRIMARY KEY,
 order_id TEXT NOT NULL REFERENCES orders(id),
 workflow_revision INTEGER NOT NULL CHECK(workflow_revision>0),
 template_id TEXT NOT NULL,
 template_version INTEGER NOT NULL CHECK(template_version>0),
 plan_id TEXT NOT NULL,
 plan_version INTEGER NOT NULL CHECK(plan_version>0),
 asset_id TEXT NOT NULL REFERENCES assets(id),
 performer_id TEXT NOT NULL REFERENCES users(id),
 supervisor_id TEXT NOT NULL REFERENCES users(id),
 submitted_by TEXT NOT NULL REFERENCES users(id),
 number TEXT NOT NULL,
 title TEXT NOT NULL,
 hall TEXT NOT NULL,
 asset_code TEXT NOT NULL,
 asset_name TEXT NOT NULL,
 performer_name TEXT NOT NULL,
 specialty TEXT NOT NULL,
 work_date TEXT NOT NULL,
 executed_at TEXT NOT NULL,
 submitted_at TEXT NOT NULL,
 body TEXT NOT NULL,
 body_sha256 TEXT NOT NULL CHECK(length(body_sha256)=64),
 evidence_source TEXT NOT NULL CHECK(evidence_source IN ('submission-snapshot','legacy-reconstructed')),
 UNIQUE(order_id,workflow_revision),
 FOREIGN KEY(template_id,template_version) REFERENCES pm_template_revisions(template_id,revision),
 FOREIGN KEY(plan_id,plan_version) REFERENCES pm_plan_revisions(plan_id,revision)
);
CREATE INDEX IF NOT EXISTS ix_pm_archive_submitted ON pm_submission_archive(submitted_at DESC,id DESC);
CREATE INDEX IF NOT EXISTS ix_pm_archive_work_date ON pm_submission_archive(work_date DESC,submitted_at DESC,id DESC);
CREATE INDEX IF NOT EXISTS ix_pm_archive_performer ON pm_submission_archive(performer_id,work_date DESC,submitted_at DESC,id DESC);
CREATE INDEX IF NOT EXISTS ix_pm_archive_template ON pm_submission_archive(template_id,work_date DESC,submitted_at DESC,id DESC);
CREATE INDEX IF NOT EXISTS ix_pm_archive_hall ON pm_submission_archive(hall,work_date DESC,submitted_at DESC,id DESC);
CREATE INDEX IF NOT EXISTS ix_pm_archive_asset ON pm_submission_archive(asset_id,work_date DESC,submitted_at DESC,id DESC);
