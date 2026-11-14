"""Master-data contract and authorization tests on a fresh current schema only."""

from __future__ import annotations

import shutil
import sqlite3
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path

from pak_maintenance.common import ApiError, User, dumps, loads, now
from pak_maintenance.database import Database, DatabaseConfig
from pak_maintenance.master_data import CatalogService, MasterDataService
from pak_maintenance.security import verify_password


class MasterDataTests(unittest.TestCase):
    def setUp(self):
        self.fixture_root = Path(__file__).resolve().parent / ".master-data-test-data"
        self.fixture_root.mkdir(mode=0o755, exist_ok=True)
        self.directory = self.fixture_root / uuid.uuid4().hex
        self.directory.mkdir(mode=0o755)
        path = self.directory / "fixture.db"
        schema = (Path(__file__).resolve().parents[1] / "schema" / "sqlite-v10.sql").read_text(encoding="utf-8")
        schema += "\n" + "\n".join(p.read_text(encoding="utf-8") for p in sorted((Path(__file__).resolve().parents[1] / "schema").glob("sqlite-*-guards.sql")))
        raw = sqlite3.connect(path)
        try:
            raw.executescript(schema)
            raw.commit()
        finally:
            raw.close()
        self.db = Database(DatabaseConfig("sqlite", str(path)))
        self.service = MasterDataService(self.db, self.directory)
        self.people = {}
        with self.db.transaction() as connection:
            for identifier, role, specialty, active in (
                ("admin", "admin", "", 1), ("admin2", "admin", "", 1), ("head", "head", "", 1),
                ("manager", "manager", "", 1), ("planner", "planner", "", 1),
                ("supervisor-m", "supervisor", "mechanical", 1), ("supervisor-e", "supervisor", "electrical", 1),
                ("tech-m", "technician", "mechanical", 1), ("tech-e", "technician", "electrical", 1),
                ("inactive", "technician", "mechanical", 0),
            ):
                connection.execute("INSERT INTO users(id,code,name,role,specialty,active,password_hash,session_epoch) VALUES(:id,:code,:name,:role,:specialty,:active,'fixture-unset-hash',0)",
                                   {"id": identifier, "code": identifier, "name": identifier, "role": role, "specialty": specialty, "active": active})
                self.people[identifier] = User(identifier, identifier, identifier, role, specialty, bool(active), "fixture-unset-hash", 0)
            connection.execute("INSERT INTO assets(id,code,name,hall,line,body,version) VALUES('machine','pump-1','Pump','Process','A',:body,1)", {"body": dumps({"manufacturer": "private metadata", "serial": "private serial"})})
            connection.execute("INSERT INTO parts(id,code,name,specification,unit,unit_cost,version) VALUES('part','warehouse-1','Bearing','Standard','pcs',12.34,1)")
        self.admin, self.head = self.people["admin"], self.people["head"]

    def tearDown(self):
        if self.directory.resolve().parent != self.fixture_root.resolve() or self.directory.is_symlink():
            raise RuntimeError("Refusing cleanup outside the owned master-data fixture.")
        shutil.rmtree(self.directory)
        try:
            self.fixture_root.rmdir()
        except OSError:
            pass

    def assert_api(self, status, operation):
        with self.assertRaises(ApiError) as raised:
            operation()
        self.assertEqual(raised.exception.status, status)

    @staticmethod
    def asset_body(**changes):
        body = {"code": "new-asset", "name": "New pump", "hall": "Packaging", "line": "B", "manufacturer": "Maker", "criticality": "high"}
        body.update(changes)
        return body

    @staticmethod
    def part_body(**changes):
        body = {"code": "warehouse-1", "name": "Bearing", "specification": "Standard", "unit": "pcs", "unitCost": Decimal("12.34"), "version": 1}
        body.update(changes)
        return body

    @staticmethod
    def user_body(**changes):
        body = {"code": "new-person", "name": "New technician", "role": "technician", "specialty": "mechanical", "active": True}
        body.update(changes)
        return body

    @staticmethod
    def plan_body(**changes):
        body = {"assetId": "machine", "title": "Monthly inspection", "description": "Check pump", "intervalDays": 30,
                "nextDueDate": "2026-10-10", "assigneeId": "tech-m", "checklist": [{"text": "Check bearing", "done": True}], "active": True}
        body.update(changes)
        return body

    def add_used_part(self, *, historical=False):
        with self.db.transaction() as connection:
            if historical:
                connection.execute("INSERT INTO stock_movements(id,part_id,quantity,kind,reference,note,actor_id,created_at,quantity_units) VALUES('historic','part',1,'historic','','','admin',:now,1000000)", {"now": now()})
            else:
                connection.execute("INSERT INTO orders(id,number,version,workflow_revision,type,status,asset_id,created_by,assignee_id,work_date,due_date,body,created_at,updated_at) VALUES('work','WO-1',1,1,'EM','draft','machine','tech-m',NULL,'2026-10-10',NULL,'{}',:now,:now)", {"now": now()})
                connection.execute("INSERT INTO order_parts(order_id,part_id,quantity,quantity_units) VALUES('work','part',1,1000000)")

    def test_catalog_service_is_same_contract_class(self):
        self.assertIs(CatalogService, MasterDataService)

    def test_paged_bootstrap_is_small_and_technician_fields_are_private(self):
        result = self.service.bootstrap(self.people["tech-m"], "paged")
        self.assertEqual(result["assets"], [])
        self.assertEqual(result["parts"], [])
        self.assertEqual(result["people"], [])
        self.assertEqual(result["catalogCounts"], {"assets": 1, "parts": 1, "people": 4})
        self.assertEqual(result["assetLines"], [{"hall": "Process", "line": "A"}])
        legacy = self.service.bootstrap(self.people["tech-m"])
        self.assertNotIn("serial", legacy["assets"][0])
        self.assertNotIn("manufacturer", legacy["assets"][0])
        self.assertTrue(all("role" not in person and "active" not in person for person in legacy["people"]))
        self.assertTrue(all(person["specialty"] for person in legacy["people"]))
        self.assertIsNone(legacy["parts"][0]["consumedQuantity"])
        self.assertIsNone(legacy["parts"][0]["unitCost"])

    def test_catalog_technician_never_reads_totals_prices_or_inactive_people(self):
        with self.db.transaction() as connection:
            connection.execute("INSERT INTO part_consumption_totals VALUES('part',1234567,4)")
        technician = self.people["tech-m"]
        part = self.service.catalog(technician, "parts")["items"][0]
        self.assertIsNone(part["consumedQuantity"])
        self.assertIsNone(part["consumedReports"])
        self.assertIsNone(part["unitCost"])
        manager_part = self.service.catalog(self.head, "parts")["items"][0]
        self.assertEqual(manager_part["consumedQuantity"], Decimal("1.234567"))
        self.assertEqual(manager_part["consumedReports"], 4)
        self.assertEqual(manager_part["unitCost"], Decimal("12.34"))
        self.assertNotIn("stock", manager_part)
        self.assertEqual(self.service.catalog(technician, "people", {"ids": "inactive", "active": "false"})["total"], 0)
        self.assertEqual(self.service.catalog(self.admin, "people", {"ids": "inactive"})["total"], 1)

    def test_catalog_literal_wildcard_search_and_injection_are_bound(self):
        self.service.save_asset(self.admin, self.asset_body(code="percent", name="Pump 100%"))
        self.service.save_asset(self.admin, self.asset_body(code="other", name="Pump 100A"))
        literal = self.service.catalog(self.admin, "assets", {"q": "100%"})
        self.assertEqual(literal["total"], 1)
        self.assertEqual(literal["items"][0]["code"], "percent")
        self.service.save_part(self.admin, self.part_body(code="literal_1"))
        self.service.save_part(self.admin, self.part_body(code="literalA1"))
        self.assertEqual(self.service.catalog(self.admin, "parts", {"q": "literal_"})["total"], 1)
        self.assertEqual(self.service.catalog(self.admin, "parts", {"ids": "part') OR 1=1 --"})["total"], 0)
        self.assertEqual(self.service.catalog(self.admin, "parts", {"ids": "part,part"})["total"], 1)

    def test_catalog_pagination_filters_and_malformed_values(self):
        for query in ({"page": "0"}, {"page": "+1"}, {"page": "۱"}, {"pageSize": "101"}, {"q": "x" * 201}, {"ids": ",".join(str(i) for i in range(101))}):
            self.assert_api(400, lambda query=query: self.service.catalog(self.admin, "assets", query))
        for query in ({"active": "yes"}, {"hasSpecialty": "1"}, {"role": "owner"}):
            self.assert_api(400, lambda query=query: self.service.catalog(self.admin, "people", query))
        self.assert_api(404, lambda: self.service.catalog(self.admin, "users; DROP TABLE users"))
        self.assertEqual(self.service.catalog(self.admin, "people", {"role": "technician", "specialty": "electrical"})["total"], 1)
        self.assertEqual(self.service.catalog(self.admin, "assets", {"hall": "Other"})["total"], 0)
        self.assertEqual(self.service.catalog(self.admin, "assets", {"hall": "Process", "line": "A"})["total"], 1)

    def test_unknown_inactive_and_technical_users_cannot_enter_management(self):
        for user in (self.people["tech-m"], self.people["supervisor-m"]):
            self.assert_api(403, lambda user=user: self.service.save_asset(user, self.asset_body()))
            self.assert_api(403, lambda user=user: self.service.save_part(user, self.part_body()))
        unknown = User("unknown", role="owner")
        self.assert_api(401, lambda: self.service.catalog(unknown, "parts"))
        self.assert_api(401, lambda: self.service.catalog(self.people["inactive"], "parts"))
        self.assertEqual(len(self.service.users(self.head)), len(self.people))
        self.assert_api(403, lambda: self.service.audit_log(self.people["planner"]))

    def test_asset_version_conflict_preserves_latest_record(self):
        created = self.service.save_asset(self.people["planner"], self.asset_body())
        self.assertEqual(created["version"], 1)
        self.assertEqual(created["criticality"], "high")
        updated = self.service.save_asset(self.people["planner"], self.asset_body(version=1, name="Latest"), created["id"])
        self.assertEqual(updated["version"], 2)
        self.assert_api(409, lambda: self.service.save_asset(self.admin, self.asset_body(version=1, name="Stale"), created["id"]))
        row = self.service.catalog(self.admin, "assets", {"ids": created["id"]})["items"][0]
        self.assertEqual(row["name"], "Latest")
        self.assert_api(400, lambda: self.service.save_asset(self.admin, self.asset_body(criticality="unknown")))
        self.assert_api(400, lambda: self.service.save_asset(self.admin, self.asset_body(version=True), created["id"]))

    def test_duplicate_codes_become_conflict_and_rollback_audit(self):
        self.assert_api(409, lambda: self.service.save_asset(self.admin, self.asset_body(code="pump-1")))
        self.assert_api(409, lambda: self.service.save_part(self.admin, self.part_body()))
        with self.db.connect() as connection:
            self.assertEqual(connection.scalar("SELECT COUNT(*) FROM assets"), 1)
            self.assertEqual(connection.scalar("SELECT COUNT(*) FROM parts"), 1)
            self.assertEqual(connection.scalar("SELECT COUNT(*) FROM audit"), 0)

    def test_mutating_stale_or_revoked_actor_fails_inside_transaction(self):
        with self.db.transaction() as connection:
            connection.execute("UPDATE users SET session_epoch=1 WHERE id='admin'")
        self.assert_api(403, lambda: self.service.save_asset(self.admin, self.asset_body()))
        self.assert_api(403, lambda: self.service.save_part(self.admin, self.part_body(code="new-part")))
        self.assert_api(403, lambda: self.service.save_user(self.admin, self.user_body()))
        with self.db.connect() as connection:
            self.assertEqual(connection.scalar("SELECT COUNT(*) FROM audit"), 0)
            self.assertEqual(connection.scalar("SELECT COUNT(*) FROM assets"), 1)

    def test_audit_failure_rolls_back_asset_write(self):
        with self.db.transaction() as connection:
            connection.execute("CREATE TRIGGER inject_audit_failure BEFORE INSERT ON audit BEGIN SELECT RAISE(ABORT,'fixture audit failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.service.save_asset(self.admin, self.asset_body())
        with self.db.connect() as connection:
            self.assertEqual(connection.scalar("SELECT COUNT(*) FROM assets"), 1)

    def test_part_identity_is_immutable_after_work_use(self):
        self.add_used_part()
        self.assert_api(409, lambda: self.service.save_part(self.admin, self.part_body(code="other-code"), "part"))
        self.assert_api(409, lambda: self.service.save_part(self.admin, self.part_body(unit="kg"), "part"))
        renamed = self.service.save_part(self.admin, self.part_body(name="Updated bearing"), "part")
        self.assertEqual(renamed["name"], "Updated bearing")
        self.assertEqual(renamed["version"], 2)
        self.assert_api(409, lambda: self.service.save_part(self.admin, self.part_body(name="Stale"), "part"))

    def test_part_identity_is_immutable_after_historical_movement(self):
        self.add_used_part(historical=True)
        self.assert_api(409, lambda: self.service.save_part(self.admin, self.part_body(code="other-code"), "part"))
        self.assert_api(409, lambda: self.service.save_part(self.admin, self.part_body(unit="kg"), "part"))

    def test_unused_part_identity_can_change_and_price_precision_is_validated(self):
        result = self.service.save_part(self.admin, self.part_body(code="renamed-code", unit="kg", stock=99999), "part")
        self.assertEqual(result["code"], "renamed-code")
        self.assertEqual(result["unit"], "kg")
        self.assertNotIn("stock", result)
        for price in (-1, "NaN", "Infinity", Decimal("0.001"), Decimal("1000000000000.01"), True):
            self.assert_api(400, lambda price=price: self.service.save_part(self.admin, self.part_body(code="invalid-price", unitCost=price)))
        zero = self.service.save_part(self.admin, self.part_body(code="zero-price", unitCost=0))
        self.assertEqual(zero["unitCost"], Decimal(0))

    def test_user_password_and_hash_are_never_public_or_audited(self):
        password = "fixture-long-password-123"
        created = self.service.save_user(self.admin, self.user_body(password=password))
        self.assertNotIn("password", created)
        self.assertNotIn("password_hash", created)
        with self.db.connect() as connection:
            encoded = connection.scalar("SELECT password_hash FROM users WHERE id=:id", {"id": created["id"]})
            self.assertTrue(verify_password(password, encoded))
            details = connection.scalar("SELECT details FROM audit WHERE entity_id=:id", {"id": created["id"]})
            self.assertNotIn(password, details)
            self.assertNotIn(encoded, details)
        self.assertTrue(all("password_hash" not in entry for entry in self.service.users(self.admin)))
        self.assert_api(400, lambda: self.service.save_user(self.admin, self.user_body(code="invalid-password", password=False)))

    def test_user_edit_revokes_sessions_and_uses_epoch_version(self):
        with self.db.transaction() as connection:
            connection.execute("INSERT INTO sessions VALUES('session-1','tech-m',0,'2099-01-01T00:00:00.0000000+00:00')")
        updated = self.service.save_user(self.admin, self.user_body(code="tech-m", name="Changed", version=0), "tech-m")
        self.assertEqual(updated["version"], 1)
        with self.db.connect() as connection:
            self.assertEqual(connection.scalar("SELECT COUNT(*) FROM sessions WHERE user_id='tech-m'"), 0)
            self.assertEqual(connection.scalar("SELECT password_hash FROM users WHERE id='tech-m'"), "fixture-unset-hash")
        self.assert_api(409, lambda: self.service.save_user(self.admin, self.user_body(code="tech-m", version=0), "tech-m"))

    def test_self_admin_removal_and_missing_technical_specialty_are_blocked(self):
        self.assert_api(400, lambda: self.service.save_user(self.admin, self.user_body(code="admin", role="admin", specialty="", active=False, version=0), "admin"))
        self.assert_api(400, lambda: self.service.save_user(self.admin, self.user_body(code="admin", role="head", specialty="", version=0), "admin"))
        self.assert_api(400, lambda: self.service.save_user(self.admin, self.user_body(specialty="")))
        self.assert_api(400, lambda: self.service.save_user(self.admin, self.user_body(role="owner")))
        self.assert_api(400, lambda: self.service.save_user(self.admin, self.user_body(active="false")))

    def test_two_admins_cannot_concurrently_remove_all_active_admins(self):
        barrier = threading.Barrier(2)
        def demote(actor, target):
            barrier.wait(timeout=5)
            try:
                self.service.save_user(actor, self.user_body(code=target, name=target, role="head", specialty="", version=0), target)
                return 200
            except ApiError as error:
                return error.status
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(demote, self.admin, "admin2")
            second = pool.submit(demote, self.people["admin2"], "admin")
            statuses = sorted((first.result(timeout=10), second.result(timeout=10)))
        self.assertEqual(statuses, [200, 403])
        with self.db.connect() as connection:
            self.assertEqual(connection.scalar("SELECT COUNT(*) FROM users WHERE role='admin' AND active=1"), 1)

    def test_users_legacy_limit_and_stable_paged_catalog(self):
        with self.db.transaction() as connection:
            connection.executemany("INSERT INTO users(id,code,name,role,specialty,active,password_hash,session_epoch) VALUES(:id,:id,'Same','technician','mechanical',1,'',0)", ({"id": f"bulk-{i:04d}"} for i in range(1001)))
        self.assert_api(413, lambda: self.service.users(self.admin))
        first = self.service.users(self.admin, paged=True, page=1, page_size=100)
        second = self.service.users(self.admin, paged=True, page=2, page_size=100)
        self.assertEqual(first["total"], 1011)
        self.assertEqual(first["totalPages"], 11)
        self.assertFalse({person["id"] for person in first["items"]} & {person["id"] for person in second["items"]})

    def test_legacy_plan_assignment_scope_and_optimistic_version(self):
        supervisor = self.people["supervisor-m"]
        created = self.service.save_plan(self.head, self.plan_body())
        self.assertFalse(created["checklist"][0]["done"])
        self.assertEqual(self.service.plans(supervisor, paged=True)["total"], 1)
        self.assertEqual(self.service.plans(self.people["supervisor-e"], paged=True)["total"], 0)
        updated = self.service.save_plan(self.head, self.plan_body(version=1, title="Updated inspection"), created["id"])
        self.assertEqual(updated["version"], 2)
        self.assert_api(409, lambda: self.service.save_plan(self.head, self.plan_body(version=1), created["id"]))
        self.assert_api(403, lambda: self.service.save_plan(supervisor, self.plan_body(assigneeId="tech-e")))
        self.assert_api(400, lambda: self.service.save_plan(self.head, self.plan_body(assigneeId="inactive")))
        self.assert_api(403, lambda: self.service.plans(self.people["tech-m"]))

    def test_supervisor_cannot_capture_other_specialty_existing_plan(self):
        created = self.service.save_plan(self.head, self.plan_body(assigneeId="tech-e"))
        self.assert_api(403, lambda: self.service.save_plan(self.people["supervisor-m"], self.plan_body(version=1, assigneeId="tech-m"), created["id"]))
        self.assert_api(400, lambda: self.service.save_plan(self.head, self.plan_body(checklist=[])))
        self.assert_api(400, lambda: self.service.save_plan(self.head, self.plan_body(intervalDays=True)))
        self.assert_api(400, lambda: self.service.save_plan(self.head, self.plan_body(nextDueDate="1405/07/18")))

    def test_legacy_import_is_archive_only_atomic_and_idempotent(self):
        data = {"summary": {"count": 2}, "manifest": {"fixture": True}, "records": [{"id": "old-1", "status": "completed"}, {"id": "old-2", "title": "Archive"}]}
        (self.directory / "legacy-data.json").write_text(dumps(data), encoding="utf-8")
        self.assertEqual(self.service.import_preview(self.head)["summary"], {"count": 2})
        self.assertEqual(self.service.import_legacy(self.admin, {"mode": "archive"}), {"inserted": 2, "activationPerformed": False})
        self.assertEqual(self.service.import_legacy(self.admin, {"mode": "archive"})["inserted"], 0)
        self.assertEqual(self.service.legacy(self.head, paged=True)["total"], 2)
        self.assert_api(400, lambda: self.service.import_legacy(self.admin, {"mode": "activate"}))
        self.assert_api(403, lambda: self.service.import_legacy(self.head, {"mode": "archive"}))
        self.assert_api(403, lambda: self.service.import_preview(self.people["tech-m"]))
        with self.db.connect() as connection:
            self.assertEqual(connection.scalar("SELECT COUNT(*) FROM orders"), 0)
            self.assertEqual(connection.scalar("SELECT COUNT(*) FROM pm_weekly_plans"), 0)

    def test_invalid_archive_does_not_partially_import_or_activate(self):
        (self.directory / "legacy-data.json").write_text(dumps({"records": [{"id": "valid"}, {"id": ""}]}), encoding="utf-8")
        self.assert_api(400, lambda: self.service.import_legacy(self.admin, {"mode": "archive"}))
        with self.db.connect() as connection:
            self.assertEqual(connection.scalar("SELECT COUNT(*) FROM legacy_records"), 0)


if __name__ == "__main__":
    unittest.main()
