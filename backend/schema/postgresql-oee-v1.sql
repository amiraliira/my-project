-- Optional OEE module v1. This rc12 delivery enables it on core schema 10.
-- Run once in a transaction after a verified native backup. No existing rows modified.
CREATE TABLE oee_module(version INTEGER PRIMARY KEY CHECK(version=1));
CREATE TABLE oee_lines(
 id TEXT PRIMARY KEY, code TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
 asset_id TEXT NULL UNIQUE REFERENCES assets(id), version INTEGER NOT NULL CHECK(version>0),
 active INTEGER NOT NULL CHECK(active IN(0,1)), body TEXT NOT NULL);
CREATE TABLE oee_sheets(
 id TEXT PRIMARY KEY, line_id TEXT NOT NULL REFERENCES oee_lines(id),
 work_date TEXT NOT NULL CHECK(work_date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'),
 shift TEXT NOT NULL CHECK(shift IN('morning','night')),
 version INTEGER NOT NULL CHECK(version>0), state TEXT NOT NULL CHECK(state IN('draft','final')),
 start_minute INTEGER NOT NULL CHECK(start_minute BETWEEN 0 AND 1439),
 end_minute INTEGER NOT NULL CHECK(end_minute>start_minute AND end_minute<start_minute+1440),
 body TEXT NOT NULL, facts TEXT NOT NULL, payload_hash TEXT NOT NULL,
 actor_id TEXT NOT NULL REFERENCES users(id), updated_at TEXT NOT NULL,
 UNIQUE(line_id,work_date,shift));
CREATE INDEX ix_oee_sheet_period ON oee_sheets(work_date,line_id,state);
CREATE TABLE oee_sheet_revisions(
 sheet_id TEXT NOT NULL REFERENCES oee_sheets(id), version INTEGER NOT NULL CHECK(version>0),
 body TEXT NOT NULL, actor_id TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL,
 PRIMARY KEY(sheet_id,version));
CREATE OR REPLACE FUNCTION pak_oee_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'OEE evidence is immutable' USING ERRCODE='23514'; END; $$;
CREATE TRIGGER oee_revisions_immutable BEFORE UPDATE OR DELETE ON oee_sheet_revisions
 FOR EACH ROW EXECUTE FUNCTION pak_oee_immutable();
CREATE TRIGGER oee_sheets_no_delete BEFORE DELETE ON oee_sheets
 FOR EACH ROW EXECUTE FUNCTION pak_oee_immutable();
INSERT INTO oee_module(version) VALUES(1);
