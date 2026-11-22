-- Schema 6 generated from a verified consistent SQLite snapshot. No company rows or passwords.
-- Explicit initialization of a NEW target database only; runtime must not migrate live data.

CREATE TABLE assets(id TEXT PRIMARY KEY,code TEXT NOT NULL UNIQUE,name TEXT NOT NULL,hall TEXT NOT NULL,line TEXT NOT NULL,body TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1);

CREATE TABLE legacy_records(id TEXT PRIMARY KEY,body TEXT NOT NULL,imported_at TEXT NOT NULL);

CREATE TABLE parts(id TEXT PRIMARY KEY,code TEXT NOT NULL UNIQUE,name TEXT NOT NULL,specification TEXT NOT NULL,unit TEXT NOT NULL,unit_cost NUMERIC NULL CHECK(unit_cost>=0), version INTEGER NOT NULL DEFAULT 1);

CREATE TABLE pm_templates(id TEXT PRIMARY KEY,title TEXT NOT NULL,specialty TEXT NOT NULL,hall TEXT NOT NULL,period TEXT NOT NULL,version INTEGER NOT NULL CHECK(version>0),mapping_status TEXT NOT NULL,body TEXT NOT NULL);

CREATE TABLE schema_version(version INTEGER NOT NULL);

CREATE TABLE users(id TEXT PRIMARY KEY,code TEXT NOT NULL UNIQUE,name TEXT NOT NULL,role TEXT NOT NULL,specialty TEXT NOT NULL,active INTEGER NOT NULL,password_hash TEXT NOT NULL,session_epoch INTEGER NOT NULL);

CREATE TABLE audit(id TEXT PRIMARY KEY,actor_id TEXT NULL REFERENCES users(id),actor_name TEXT NOT NULL,action TEXT NOT NULL,entity TEXT NOT NULL,entity_id TEXT NOT NULL,details TEXT NOT NULL,created_at TEXT NOT NULL);

CREATE TABLE orders(id TEXT PRIMARY KEY,number TEXT NOT NULL UNIQUE,version INTEGER NOT NULL,workflow_revision INTEGER NOT NULL,type TEXT NOT NULL CHECK(type IN ('PM','EM','PROJECT')),status TEXT NOT NULL,asset_id TEXT NOT NULL REFERENCES assets(id),created_by TEXT NOT NULL REFERENCES users(id),assignee_id TEXT NULL REFERENCES users(id),work_date TEXT NOT NULL,due_date TEXT NULL,body TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL, duration_minutes INTEGER NOT NULL DEFAULT 0, title TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '');

CREATE TABLE part_consumption_totals(part_id TEXT PRIMARY KEY REFERENCES parts(id),quantity_units BIGINT NOT NULL CHECK(quantity_units>0),report_count BIGINT NOT NULL CHECK(report_count>0));

CREATE TABLE plans(id TEXT PRIMARY KEY,asset_id TEXT NOT NULL REFERENCES assets(id),next_due TEXT NOT NULL,interval_days INTEGER NOT NULL CHECK(interval_days>0),active INTEGER NOT NULL,version INTEGER NOT NULL,body TEXT NOT NULL);

CREATE TABLE pm_setup_requests(actor_id TEXT NOT NULL REFERENCES users(id),request_key TEXT NOT NULL,payload_hash TEXT NOT NULL,body TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(actor_id,request_key));

CREATE TABLE pm_template_revisions(template_id TEXT NOT NULL REFERENCES pm_templates(id),revision INTEGER NOT NULL,body TEXT NOT NULL,reason TEXT NOT NULL,actor_id TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,PRIMARY KEY(template_id,revision));

CREATE TABLE pm_weekly_plans(id TEXT PRIMARY KEY,template_id TEXT NOT NULL REFERENCES pm_templates(id),asset_id TEXT NULL REFERENCES assets(id),supervisor_id TEXT NULL REFERENCES users(id),specialty TEXT NOT NULL,hall TEXT NOT NULL,version INTEGER NOT NULL CHECK(version>0),active INTEGER NOT NULL CHECK(active IN (0,1)),mapping_confirmed INTEGER NOT NULL CHECK(mapping_confirmed IN (0,1)),start_date TEXT NOT NULL,cursor_month INTEGER NOT NULL,next_scan TEXT NOT NULL,body TEXT NOT NULL);

CREATE TABLE sessions(id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),epoch INTEGER NOT NULL,expires TEXT NOT NULL);

CREATE TABLE stock_movements(id TEXT PRIMARY KEY,part_id TEXT NOT NULL REFERENCES parts(id),quantity NUMERIC NOT NULL,kind TEXT NOT NULL,reference TEXT NOT NULL,note TEXT NOT NULL,actor_id TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL, quantity_units BIGINT NOT NULL DEFAULT 0);

CREATE TABLE approvals(id TEXT PRIMARY KEY,order_id TEXT NOT NULL REFERENCES orders(id),revision INTEGER NOT NULL,stage TEXT NOT NULL,specialty TEXT NOT NULL,decision TEXT NOT NULL,coefficient NUMERIC NULL,reason TEXT NOT NULL,actor_id TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL);

CREATE TABLE completed_scores(order_id TEXT NOT NULL REFERENCES orders(id),revision INTEGER NOT NULL,person_id TEXT NOT NULL REFERENCES users(id),supervisor_coefficient NUMERIC NULL,head_coefficient NUMERIC NULL,score_raw TEXT NULL,score_cents BIGINT NULL,source TEXT NULL,PRIMARY KEY(order_id,revision,person_id));

CREATE TABLE order_parts(order_id TEXT NOT NULL REFERENCES orders(id),part_id TEXT NOT NULL REFERENCES parts(id),quantity NUMERIC NOT NULL CHECK(quantity>0), quantity_units BIGINT NOT NULL DEFAULT 0,PRIMARY KEY(order_id,part_id));

CREATE TABLE participants(order_id TEXT NOT NULL REFERENCES orders(id),person_id TEXT NOT NULL REFERENCES users(id),minutes INTEGER NOT NULL CHECK(minutes>0),specialty TEXT NOT NULL,PRIMARY KEY(order_id,person_id));

CREATE TABLE pm_occurrences(plan_id TEXT NOT NULL REFERENCES plans(id),due_date TEXT NOT NULL,order_id TEXT NOT NULL REFERENCES orders(id),PRIMARY KEY(plan_id,due_date));

CREATE TABLE pm_plan_revisions(plan_id TEXT NOT NULL REFERENCES pm_weekly_plans(id),revision INTEGER NOT NULL,body TEXT NOT NULL,reason TEXT NOT NULL,actor_id TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,PRIMARY KEY(plan_id,revision));

CREATE TABLE pm_weekly_assets(order_id TEXT NOT NULL REFERENCES orders(id),asset_id TEXT NOT NULL REFERENCES assets(id),PRIMARY KEY(order_id,asset_id));

CREATE TABLE request_claims(actor_id TEXT NOT NULL REFERENCES users(id),request_key TEXT NOT NULL,order_id TEXT NOT NULL REFERENCES orders(id),payload_hash TEXT NOT NULL,PRIMARY KEY(actor_id,request_key));

CREATE TABLE stock_request_claims(actor_id TEXT NOT NULL REFERENCES users(id),request_key TEXT NOT NULL,movement_id TEXT NOT NULL REFERENCES stock_movements(id),payload_hash TEXT NOT NULL,PRIMARY KEY(actor_id,request_key));

CREATE TABLE part_consumptions(id TEXT PRIMARY KEY,order_id TEXT NOT NULL REFERENCES orders(id),part_id TEXT NOT NULL REFERENCES parts(id),approval_id TEXT NOT NULL REFERENCES approvals(id),revision INTEGER NOT NULL CHECK(revision>0),quantity_units BIGINT NOT NULL CHECK(quantity_units>0),part_code TEXT NOT NULL,part_name TEXT NOT NULL,unit TEXT NOT NULL,order_number TEXT NOT NULL,executed_at TEXT NOT NULL,approved_at TEXT NOT NULL,actor_id TEXT NOT NULL REFERENCES users(id),actor_name TEXT NOT NULL,UNIQUE(order_id,part_id));

CREATE TABLE participant_scores(order_id TEXT NOT NULL REFERENCES orders(id),revision INTEGER NOT NULL,person_id TEXT NOT NULL REFERENCES users(id),specialty TEXT NOT NULL,coefficient NUMERIC NOT NULL CHECK(coefficient>=0 AND coefficient<=3),actor_id TEXT NOT NULL REFERENCES users(id),approval_id TEXT NOT NULL REFERENCES approvals(id),created_at TEXT NOT NULL,PRIMARY KEY(order_id,revision,person_id));

CREATE TABLE pm_weekly_occurrences(plan_id TEXT NOT NULL REFERENCES pm_weekly_plans(id),persian_year INTEGER NOT NULL,month INTEGER NOT NULL CHECK(month BETWEEN 1 AND 12),week INTEGER NOT NULL CHECK(week BETWEEN 1 AND 4),order_id TEXT NOT NULL UNIQUE REFERENCES orders(id),plan_version INTEGER NOT NULL,template_id TEXT NOT NULL,template_version INTEGER NOT NULL,start_utc TEXT NOT NULL,end_utc TEXT NOT NULL,specialty TEXT NOT NULL,supervisor_id TEXT NOT NULL REFERENCES users(id),submitted_at TEXT NULL,executed_at TEXT NULL,PRIMARY KEY(plan_id,persian_year,month,week),FOREIGN KEY(template_id,template_version) REFERENCES pm_template_revisions(template_id,revision),FOREIGN KEY(plan_id,plan_version) REFERENCES pm_plan_revisions(plan_id,revision));

CREATE INDEX ix_approval_order ON approvals(order_id,revision);
CREATE INDEX ix_approvals_stage ON approvals(order_id,revision,stage,decision,specialty,created_at DESC,id DESC);
CREATE INDEX ix_assets_location ON assets(hall,line,code,id);
CREATE INDEX ix_assets_name ON assets(name,id);
CREATE INDEX ix_audit_created ON audit(created_at DESC,id DESC);
CREATE INDEX ix_audit_entity ON audit(entity,entity_id,created_at,id);
CREATE INDEX ix_completed_scores_person ON completed_scores(person_id,order_id,revision);
CREATE INDEX ix_consumption_date ON part_consumptions(executed_at DESC,id DESC);
CREATE INDEX ix_consumption_part_date ON part_consumptions(part_id,executed_at DESC,id DESC);
CREATE INDEX ix_movements_created ON stock_movements(created_at DESC,id DESC);
CREATE INDEX ix_movements_part ON stock_movements(part_id,created_at);
CREATE INDEX ix_orders_asset_created ON orders(asset_id,created_at DESC,id DESC);
CREATE INDEX ix_orders_assignee ON orders(assignee_id,status,created_at DESC,id DESC);
CREATE INDEX ix_orders_created ON orders(created_at DESC,id DESC);
CREATE INDEX ix_orders_creator ON orders(created_by,created_at DESC,id DESC);
CREATE INDEX ix_orders_inbox ON orders(status,updated_at,created_at,id);
CREATE INDEX ix_orders_performance_date ON orders(status,work_date,id);
CREATE INDEX ix_orders_status ON orders(status,due_date);
CREATE INDEX ix_participant_scores_approval ON participant_scores(approval_id);
CREATE INDEX ix_participants_person ON participants(person_id,specialty,order_id);
CREATE INDEX ix_participants_specialty ON participants(specialty,order_id);
CREATE INDEX ix_parts_name ON parts(name,id);
CREATE INDEX ix_plans_due ON plans(active,next_due,id);
CREATE INDEX ix_pm_templates_filter ON pm_templates(specialty,hall,title,id);
CREATE INDEX ix_pm_weekly_assets_asset ON pm_weekly_assets(asset_id,order_id);
CREATE INDEX ix_pm_weekly_deadline ON pm_weekly_occurrences(end_utc,order_id);
CREATE INDEX ix_pm_weekly_due ON pm_weekly_plans(active,next_scan,id);
CREATE INDEX ix_pm_weekly_owner ON pm_weekly_occurrences(supervisor_id,end_utc,order_id);
CREATE INDEX ix_sessions_expires ON sessions(expires);
CREATE INDEX ix_sessions_user ON sessions(user_id,expires DESC,id);
CREATE INDEX ix_users_specialty_active ON users(specialty,active,id);

INSERT INTO schema_version(version) VALUES(6);

-- Register exact score aggregate/functions from reporting_service.POSTGRESQL_FUNCTIONS before use.
