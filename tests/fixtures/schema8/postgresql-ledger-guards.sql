-- Explicit migration/init account only. Runtime services never execute DDL.
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conname='ck_order_parts_units' AND conrelid='order_parts'::regclass) THEN
  ALTER TABLE order_parts ADD CONSTRAINT ck_order_parts_units CHECK(quantity_units>0);
 END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conname='ck_movements_units' AND conrelid='stock_movements'::regclass) THEN
  ALTER TABLE stock_movements ADD CONSTRAINT ck_movements_units CHECK(quantity_units<>0);
 END IF;
END; $$;
CREATE OR REPLACE FUNCTION pak_guard_consumption_history() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'approved consumption history is immutable' USING ERRCODE='23514'; END;
$$;
DROP TRIGGER IF EXISTS guard_consumption_history ON part_consumptions;
CREATE TRIGGER guard_consumption_history BEFORE UPDATE OR DELETE ON part_consumptions
FOR EACH ROW EXECUTE FUNCTION pak_guard_consumption_history();
DROP TRIGGER IF EXISTS guard_consumption_total_delete ON part_consumption_totals;
CREATE TRIGGER guard_consumption_total_delete BEFORE DELETE ON part_consumption_totals
FOR EACH ROW EXECUTE FUNCTION pak_guard_consumption_history();
CREATE OR REPLACE FUNCTION pak_guard_consumption_total_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.part_id IS DISTINCT FROM OLD.part_id OR NEW.quantity_units<OLD.quantity_units OR NEW.report_count<OLD.report_count
 THEN RAISE EXCEPTION 'approved consumption totals cannot decrease or change identity' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END; $$;
DROP TRIGGER IF EXISTS guard_consumption_total_update ON part_consumption_totals;
CREATE TRIGGER guard_consumption_total_update BEFORE UPDATE ON part_consumption_totals
FOR EACH ROW EXECUTE FUNCTION pak_guard_consumption_total_update();
DROP TRIGGER IF EXISTS guard_completed_scores_history ON completed_scores;
CREATE TRIGGER guard_completed_scores_history BEFORE UPDATE OR DELETE ON completed_scores
FOR EACH ROW EXECUTE FUNCTION pak_guard_consumption_history();
