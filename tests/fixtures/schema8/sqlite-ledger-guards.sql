-- Explicit migration/init account only. Runtime services never execute DDL.
CREATE TRIGGER IF NOT EXISTS guard_order_parts_units_insert BEFORE INSERT ON order_parts
WHEN typeof(NEW.quantity_units)<>'integer' OR NEW.quantity_units<=0
BEGIN SELECT RAISE(ABORT,'invalid part quantity units'); END;
CREATE TRIGGER IF NOT EXISTS guard_order_parts_units_update BEFORE UPDATE OF quantity_units ON order_parts
WHEN typeof(NEW.quantity_units)<>'integer' OR NEW.quantity_units<=0
BEGIN SELECT RAISE(ABORT,'invalid part quantity units'); END;
CREATE TRIGGER IF NOT EXISTS guard_movements_units_insert BEFORE INSERT ON stock_movements
WHEN typeof(NEW.quantity_units)<>'integer' OR NEW.quantity_units=0
BEGIN SELECT RAISE(ABORT,'invalid movement quantity units'); END;
CREATE TRIGGER IF NOT EXISTS guard_movements_units_update BEFORE UPDATE OF quantity_units ON stock_movements
WHEN typeof(NEW.quantity_units)<>'integer' OR NEW.quantity_units=0
BEGIN SELECT RAISE(ABORT,'invalid movement quantity units'); END;
CREATE TRIGGER IF NOT EXISTS guard_consumption_units_insert BEFORE INSERT ON part_consumptions
WHEN typeof(NEW.quantity_units)<>'integer' OR NEW.quantity_units<=0
BEGIN SELECT RAISE(ABORT,'invalid consumption quantity units'); END;
CREATE TRIGGER IF NOT EXISTS guard_consumption_history_update BEFORE UPDATE ON part_consumptions
BEGIN SELECT RAISE(ABORT,'approved consumption history is immutable'); END;
CREATE TRIGGER IF NOT EXISTS guard_consumption_history_delete BEFORE DELETE ON part_consumptions
BEGIN SELECT RAISE(ABORT,'approved consumption history is immutable'); END;
CREATE TRIGGER IF NOT EXISTS guard_consumption_total_insert BEFORE INSERT ON part_consumption_totals
WHEN typeof(NEW.quantity_units)<>'integer' OR NEW.quantity_units<=0 OR typeof(NEW.report_count)<>'integer' OR NEW.report_count<=0
BEGIN SELECT RAISE(ABORT,'invalid consumption total'); END;
CREATE TRIGGER IF NOT EXISTS guard_consumption_total_update BEFORE UPDATE ON part_consumption_totals
WHEN NEW.part_id<>OLD.part_id OR typeof(NEW.quantity_units)<>'integer' OR NEW.quantity_units<OLD.quantity_units OR typeof(NEW.report_count)<>'integer' OR NEW.report_count<OLD.report_count
BEGIN SELECT RAISE(ABORT,'invalid consumption total'); END;
CREATE TRIGGER IF NOT EXISTS guard_consumption_total_delete BEFORE DELETE ON part_consumption_totals
BEGIN SELECT RAISE(ABORT,'approved consumption totals cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS guard_completed_scores_update BEFORE UPDATE ON completed_scores
BEGIN SELECT RAISE(ABORT,'completed score evidence is immutable'); END;
CREATE TRIGGER IF NOT EXISTS guard_completed_scores_delete BEFORE DELETE ON completed_scores
BEGIN SELECT RAISE(ABORT,'completed score evidence is immutable'); END;
