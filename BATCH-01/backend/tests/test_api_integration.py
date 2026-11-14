"""Real router/session/domain integration using synthetic guarded schema 7.

Uses a real isolated loopback PostgreSQL database with verified TLS and only
synthetic rows. Router dispatch does not exercise browser, ASGI HTTP middleware
or company LAN latency; those remain separate acceptance gates.
"""
from collections import defaultdict
import csv
from datetime import datetime, timezone
from decimal import Decimal
import gc
from http.cookies import SimpleCookie
from io import StringIO
import json
import os
from pathlib import Path
import platform
import shutil
import statistics
import time
import tracemalloc
import unittest
from uuid import uuid4

from pak_maintenance.api import Api, Request
from pak_maintenance.application import compose
from pak_maintenance.common import ApiError, dumps, now
from pak_maintenance.configuration import AppConfig
from pak_maintenance.database import DatabaseConfig
from pak_maintenance.security import COOKIE, hash_password
from pg_test_support import fresh_database


PROJECT = Path(__file__).resolve().parent.parent
FIXTURE_ROOT = Path(__file__).resolve().parent / ".api-integration-fixtures"


class ApiIntegrationTests(unittest.TestCase):
    password = "Synthetic-Only-123!"

    @classmethod
    def setUpClass(cls):
        cls.password_hash = hash_password(cls.password)

    def setUp(self):
        self.directory = FIXTURE_ROOT / uuid4().hex
        self.directory.mkdir(parents=True, mode=0o755)
        for name in ("frontend", "seed", "backups"):
            (self.directory / name).mkdir(mode=0o755)
        database_config, self.owner = fresh_database(self, prefix="api")
        self.config = AppConfig(database=database_config,
                    listen_url="http://127.0.0.1:5019", frontend_directory=str(self.directory / "frontend"),
                    seed_directory=str(self.directory / "seed"), backup_directory=str(self.directory / "backups"),
                    development=True, scheduling_enabled=False)
        self.services = compose(self.config)
        self.api = Api(self.services, self.config)
        self.cookies = {}
        users = (("m1", "technician", "مکانیک"), ("m2", "technician", "مکانیک"),
                 ("e1", "technician", "برق"), ("outside", "technician", "برق"),
                 ("sm", "supervisor", "مکانیک"), ("se", "supervisor", "برق"),
                 ("head", "head", ""), ("admin", "admin", ""), ("planner", "planner", ""))
        with self.services.db.transaction() as c:
            for identifier, role, specialty in users:
                c.execute("""INSERT INTO users(id,code,name,role,specialty,active,password_hash,session_epoch)
VALUES(:id,:id,:name,:role,:specialty,1,:password,0)""", {"id": identifier, "name": "کاربر آزمایشی " + identifier,
                    "role": role, "specialty": specialty, "password": self.password_hash})
            c.execute("INSERT INTO assets(id,code,name,hall,line,body) VALUES('asset','SYN-A1','دستگاه آزمایشی','سالن آزمایشی','خط یک','{}')")
            c.execute("INSERT INTO parts(id,code,name,specification,unit,unit_cost) VALUES('part','SYN-P1','قطعه آزمایشی','','عدد',123)")
        self.services.db.verify_schema()

    def tearDown(self):
        resolved = self.directory.resolve()
        if not resolved.is_relative_to(FIXTURE_ROOT.resolve()) or resolved == FIXTURE_ROOT.resolve():
            raise RuntimeError("Refusing to remove a path outside synthetic test fixtures")
        shutil.rmtree(resolved)
        if FIXTURE_ROOT.exists() and not any(FIXTURE_ROOT.iterdir()):
            FIXTURE_ROOT.rmdir()

    def request(self, method, path, *, actor=None, query=None, body=None, headers=None):
        values = {"host": "127.0.0.1:5019", "x-app-request": "1"}
        if actor is not None:
            values["cookie"] = self.cookies[actor]
        if body is not None:
            values["content-type"] = "application/json"
        if headers:
            values.update(headers)
        request = Request(method, path, query or {}, values,
                          b"" if body is None else dumps(body).encode("utf-8"), "127.0.0.1", False)
        return self.api.dispatch(request)

    def login(self, actor):
        result = self.request("POST", "/api/auth/login", body={"code": actor, "password": self.password})
        cookie = SimpleCookie()
        cookie.load(dict(result.headers)["set-cookie"])
        self.cookies[actor] = f"{COOKIE}={cookie[COOKIE].value}"
        self.assertEqual(result.payload["user"]["id"], actor)
        return result.payload["user"]

    @staticmethod
    def work_body():
        return {"type": "EM", "assetId": "asset", "title": "تعمیر مشترک آزمایشی", "description": "آزمون گردش کار مشترک برق و مکانیک",
                "workDate": "2026-10-10", "startTime": "08:00", "endTime": "09:00", "participants": [
                    {"personId": "m1", "minutes": 60}, {"personId": "m2", "minutes": 60}, {"personId": "e1", "minutes": 60}],
                "parts": [{"partId": "part", "quantity": Decimal("1.000001")}], "checklist": [], "downtimeMinutes": 10}

    def create_and_submit(self):
        self.login("m1")
        row = self.request("POST", "/api/orders", actor="m1", body=self.work_body()).payload
        return self.request("POST", f"/api/orders/{row['id']}/submit", actor="m1", body={"version": row["version"]}).payload

    def approve(self, actor, row, grades=None, coefficient=None):
        body = {"version": row["version"], "decision": "approve"}
        if grades is not None:
            body["grades"] = [{"personId": person, "coefficient": Decimal(str(value))} for person, value in grades]
        if coefficient is not None:
            body["participantScores"] = [{"personId": p["personId"], "coefficient": Decimal(str(coefficient))} for p in row["participants"]]
        return self.request("POST", f"/api/orders/{row['id']}/decision", actor=actor, body=body).payload

    def test_full_login_reporting_workflow_two_specialties_and_individual_scores(self):
        row = self.create_and_submit()
        for actor in ("sm", "se", "head"):
            self.login(actor)
        self.assertEqual(row["status"], "supervisor")
        self.assertEqual(self.request("GET", "/api/inbox", actor="sm", query={"page": "1", "pageSize": "25"}).payload["total"], 1)
        self.assertEqual(self.request("GET", "/api/inbox", actor="head", query={"page": "1"}).payload["total"], 0)
        row = self.approve("sm", row, [("m1", 0), ("m2", "0.75")])
        self.assertEqual(row["status"], "supervisor")
        self.assertEqual(self.request("GET", "/api/inbox", actor="sm", query={"page": "1"}).payload["total"], 0)
        row = self.approve("se", row, [("e1", "2.5")])
        self.assertEqual(row["status"], "head")
        detail = self.request("GET", f"/api/orders/{row['id']}", actor="head").payload
        self.assertEqual({g["personId"]: g["coefficient"] for g in detail["participantScores"]},
                         {"m1": Decimal(0), "m2": Decimal("0.75"), "e1": Decimal("2.5")})
        completed = self.approve("head", row, coefficient=3)
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["score"], Decimal("9.75"))
        for actor in ("m1", "sm"):
            own = self.request("GET", "/api/performance", actor=actor, query={"period": "all"}).payload
            self.assertEqual(own["scope"], "own")
            self.assertEqual({p["personId"] for p in own["people"]}, {actor})
            self.assertTrue(all(p["personId"] == actor for p in own["details"]))
            with self.subTest(actor=actor), self.assertRaises(ApiError) as denied:
                self.request("GET", "/api/performance", actor=actor, query={"period": "all", "scope": "all"})
            self.assertEqual(denied.exception.status, 403)
        all_report = self.request("GET", "/api/performance", actor="head", query={"period": "all", "detailPageSize": "1"}).payload
        self.assertEqual(all_report["totals"]["score"], Decimal("9.75"))
        self.assertEqual((all_report["detailTotal"], len(all_report["details"])), (3, 1))
        consumption = self.request("GET", "/api/consumption", actor="head", query={"page": "1", "pageSize": "1"}).payload
        self.assertEqual(consumption["total"], 1)
        self.assertEqual(consumption["items"][0]["quantity"], Decimal("1.000001"))
        self.assertEqual(self.request("GET", "/api/dashboard", actor="head").payload["score"], Decimal("9.75"))
        with self.services.db.connect() as c:
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM pg_constraint WHERE connamespace='public'::regnamespace AND NOT convalidated"), 0)

    def test_individual_chief_grades_and_personnel_csv_are_head_only(self):
        row = self.create_and_submit()
        for actor in ("sm", "se", "head", "planner", "admin"):
            self.login(actor)
        row = self.approve("sm", row, [("m1", 1), ("m2", 2)])
        row = self.approve("se", row, [("e1", 3)])
        body = {"version": row["version"], "decision": "approve", "participantScores": [
            {"personId": "m1", "coefficient": 0}, {"personId": "m2", "coefficient": Decimal("1.25")},
            {"personId": "e1", "coefficient": Decimal("2.75")},
        ]}
        completed = self.request("POST", f"/api/orders/{row['id']}/decision", actor="head", body=body).payload
        self.assertEqual({p["personId"]: p["finalScore"] for p in completed["participants"]}, {"m1": Decimal(0), "m2": Decimal("2.50"), "e1": Decimal("8.25")})
        result = self.request("GET", "/api/reports/people.csv", actor="head")
        try:
            records = list(csv.reader(StringIO(b"".join(result.stream).decode("utf-8-sig"))))
        finally:
            result.stream.close()
        by_person = {record[2]: record for record in records[1:]}
        self.assertEqual(by_person["کاربر آزمایشی m1"][6:], ["0", "0.00"])
        self.assertEqual(by_person["کاربر آزمایشی m2"][6:], ["1.25", "2.50"])
        self.assertEqual(by_person["کاربر آزمایشی e1"][6:], ["2.75", "8.25"])
        for actor in ("m1", "sm", "planner", "admin"):
            with self.subTest(actor=actor):
                detail = self.request("GET", f"/api/orders/{row['id']}", actor=actor).payload
                self.assertIsNone(detail["score"])
                self.assertEqual(detail["participantHeadScores"], [])
                self.assertTrue(all(p["headCoefficient"] is None and p["finalScore"] is None for p in detail["participants"]))
                for path in ("/api/performance", "/api/reports/people.csv"):
                    with self.assertRaises(ApiError) as denied:
                        self.request("GET", path, actor=actor, query={"period": "all", "scope": "all"})
                    self.assertEqual(denied.exception.status, 403)

    def test_api_denies_anonymous_unknown_routes_cross_user_grades_and_restricted_roles(self):
        with self.assertRaises(ApiError) as error:
            self.request("GET", "/api/orders", query={"page": "1"})
        self.assertEqual(error.exception.status, 401)
        row = self.create_and_submit()
        for actor in ("outside", "sm", "admin", "planner"):
            self.login(actor)
        denied = [("GET", "/api/dashboard", "m1", None, None),
                  ("GET", "/api/users", "m1", None, None),
                  ("GET", "/api/consumption", "m1", None, None),
                  ("GET", "/api/performance", "planner", {"scope": "all"}, None),
                  ("GET", "/api/new-unmarked-route", "m1", None, None),
                  ("GET", f"/api/orders/{row['id']}", "outside", None, None),
                  ("GET", "/api/performance", "m1", {"period": "all", "personId": "e1"}, None),
                  ("POST", f"/api/orders/{row['id']}/decision", "m1", None, {"version": row["version"], "decision": "approve", "grades": []}),
                  ("POST", f"/api/orders/{row['id']}/decision", "sm", None, {"version": row["version"], "decision": "approve", "grades": [{"personId": "e1", "coefficient": 3}]}),
                  ("POST", f"/api/orders/{row['id']}/decision", "admin", None, {"version": row["version"], "decision": "approve", "grades": []})]
        for method, path, actor, query, body in denied:
            with self.subTest(method=method, path=path, actor=actor):
                with self.assertRaises(ApiError) as error:
                    self.request(method, path, actor=actor, query=query, body=body)
                self.assertEqual(error.exception.status, 403)
        with self.services.db.connect() as c:
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM approvals"), 0)
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM part_consumptions"), 0)

    def test_api_pagination_search_stale_version_and_idempotency(self):
        self.login("m1")
        first = self.request("POST", "/api/orders", actor="m1", body=self.work_body(), headers={"x-idempotency-key": "api-once"}).payload
        replay = self.request("POST", "/api/orders", actor="m1", body=self.work_body(), headers={"x-idempotency-key": "api-once"}).payload
        self.assertEqual(first["id"], replay["id"])
        with self.assertRaises(ApiError) as error:
            self.request("POST", "/api/orders", actor="m1", body=dict(self.work_body(), title="تغییر محتوا"), headers={"x-idempotency-key": "api-once"})
        self.assertEqual(error.exception.status, 409)
        for title in ("آزمایش دوم", "علامت %_ آزمایشی"):
            self.request("POST", "/api/orders", actor="m1", body=dict(self.work_body(), title=title))
        pages = [self.request("GET", "/api/orders", actor="m1", query={"page": str(page), "pageSize": "1"}).payload for page in range(1, 4)]
        self.assertEqual([p["total"] for p in pages], [3, 3, 3])
        self.assertEqual(len({p["orders"][0]["id"] for p in pages}), 3)
        literal = self.request("GET", "/api/orders", actor="m1", query={"page": "1", "q": "%_"}).payload
        self.assertEqual(literal["total"], 1)
        updated = self.request("PUT", f"/api/orders/{first['id']}", actor="m1", body=dict(self.work_body(), version=first["version"], description="شرح تازه آزمایشی")).payload
        with self.assertRaises(ApiError) as error:
            self.request("POST", f"/api/orders/{first['id']}/submit", actor="m1", body={"version": first["version"]})
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(self.request("GET", f"/api/orders/{first['id']}", actor="m1").payload["version"], updated["version"])
        for query in ({"page": "0"}, {"page": "1", "pageSize": "101"}, {"page": "nan"}, {"page": "1", "overdue": "yes"}):
            with self.subTest(query=query):
                with self.assertRaises(ApiError) as error:
                    self.request("GET", "/api/orders", actor="m1", query=query)
                self.assertEqual(error.exception.status, 400)

    def test_current_role_or_password_change_invalidates_existing_api_session(self):
        self.login("m1")
        self.assertEqual(self.request("GET", "/api/auth/me", actor="m1").payload["user"]["role"], "technician")
        with self.services.db.transaction() as c:
            c.execute("UPDATE users SET active=0,session_epoch=session_epoch+1 WHERE id='m1'")
        with self.assertRaises(ApiError) as error:
            self.request("GET", "/api/orders", actor="m1", query={"page": "1"})
        self.assertEqual(error.exception.status, 401)

    def test_approved_usage_csv_does_not_require_prices_or_expose_stock_balance(self):
        with self.services.db.transaction() as c:
            c.execute("UPDATE parts SET unit_cost=NULL WHERE id='part'")
        row = self.create_and_submit()
        for actor in ("sm", "se", "head"):
            self.login(actor)
        row = self.approve("sm", row, [("m1", 1), ("m2", 1)])
        row = self.approve("se", row, [("e1", 1)])
        self.approve("head", row, coefficient=1)
        result = self.request("GET", "/api/reports/consumption.csv", actor="head")
        try:
            text = b"".join(result.stream).decode("utf-8-sig")
        finally:
            result.stream.close()
        records = list(csv.reader(StringIO(text)))
        self.assertEqual(records, [
            ["کد انبار", "نام قطعه", "مشخصات فنی", "واحد", "مجموع مصرف تأییدشده", "تعداد گزارش نهایی"],
            ["SYN-P1", "قطعه آزمایشی", "", "عدد", "1.000001", "1"],
        ])


class SyntheticLoadMeasurement(unittest.TestCase):
    password = ApiIntegrationTests.password
    setUpClass = classmethod(ApiIntegrationTests.setUpClass.__func__)
    setUp = ApiIntegrationTests.setUp
    tearDown = ApiIntegrationTests.tearDown
    request = ApiIntegrationTests.request
    login = ApiIntegrationTests.login

    def test_ten_thousand_guarded_synthetic_reports_and_catalog_records(self):
        count, stamp = 10_000, now()
        body = dumps({"type": "EM", "assetId": "asset", "title": "گزارش ساختگی آزمون ظرفیت", "description": "اطلاعات مصنوعی؛ فاقد داده شرکت",
                      "workDate": "2026-10-10", "startTime": "08:00", "endTime": "09:00", "durationMinutes": 60,
                      "assigneeId": "m1", "participants": [{"personId": "m1", "minutes": 60}], "parts": [],
                      "checklist": [], "downtimeMinutes": 0, "productionStopped": False})
        seed_started = time.perf_counter()
        with self.services.db.transaction() as c:
            c.executemany("INSERT INTO parts(id,code,name,specification,unit,unit_cost) VALUES(:id,:code,'قطعه مصنوعی','','عدد',NULL)",
                          ({"id": f"load-part-{i:05d}", "code": f"LOAD-P-{i:05d}"} for i in range(count)))
            c.executemany("""INSERT INTO orders(id,number,version,workflow_revision,type,status,asset_id,created_by,assignee_id,work_date,due_date,body,title,description,duration_minutes,created_at,updated_at)
VALUES(:id,:number,4,2,'EM','head','asset','m1','m1','2026-10-10',NULL,:body,'گزارش ساختگی آزمون ظرفیت','فاقد داده شرکت',60,:stamp,:stamp)""",
                          ({"id": f"load-order-{i:05d}", "number": f"SYN-LOAD-{i:05d}", "body": body, "stamp": stamp} for i in range(count)))
            c.executemany("INSERT INTO participants(order_id,person_id,minutes,specialty) VALUES(:id,'m1',60,'مکانیک')",
                          ({"id": f"load-order-{i:05d}"} for i in range(count)))
            c.executemany("""INSERT INTO approvals(id,order_id,revision,stage,specialty,decision,coefficient,reason,actor_id,created_at)
VALUES(:id,:order,2,:stage,:specialty,'approve',:coefficient,'synthetic benchmark',:actor,:stamp)""",
                          ({"id": f"load-{stage}-{i:05d}", "order": f"load-order-{i:05d}", "stage": stage,
                            "specialty": "مکانیک" if stage == "supervisor" else "", "coefficient": None,
                            "actor": "sm" if stage == "supervisor" else "head", "stamp": stamp} for i in range(count) for stage in ("supervisor", "head")))
            c.executemany("""INSERT INTO participant_scores(order_id,revision,person_id,specialty,coefficient,actor_id,approval_id,created_at)
VALUES(:order,2,'m1','مکانیک',1,'sm',:approval,:stamp)""", ({"order": f"load-order-{i:05d}", "approval": f"load-supervisor-{i:05d}", "stamp": stamp} for i in range(count)))
            c.executemany("""INSERT INTO participant_head_scores(order_id,revision,person_id,specialty,coefficient,actor_id,approval_id,created_at)
VALUES(:order,2,'m1','مکانیک','1','head',:approval,:stamp)""", ({"order": f"load-order-{i:05d}", "approval": f"load-head-{i:05d}", "stamp": stamp} for i in range(count)))
            c.execute("UPDATE orders SET status='completed' WHERE status='head'")
            c.executemany("""INSERT INTO completed_scores(order_id,revision,person_id,supervisor_coefficient,head_coefficient,head_coefficient_exact,score_raw,score_cents,source)
VALUES(:order,2,'m1',1,1,'1','1',100,'individual')""", ({"order": f"load-order-{i:05d}"} for i in range(count)))
        # Match the explicit owner-run import's statistics refresh. PostgreSQL
        # cannot plan a just-loaded empty database from autovacuum statistics
        # that have not been collected yet. Runtime role never gets owner DDL.
        with self.owner.transaction() as c:
            c.execute("ANALYZE")
        seed_seconds = time.perf_counter() - seed_started
        self.login("m1")
        self.login("head")
        gc.collect()
        metrics = {}
        operations = {
            "orders_page_100": lambda: self.request("GET", "/api/orders", actor="m1", query={"page": "1", "pageSize": "100"}).payload,
            "parts_page_100": lambda: self.request("GET", "/api/catalog/parts", actor="head", query={"page": "1", "pageSize": "100"}).payload,
            "performance_details_100": lambda: self.request("GET", "/api/performance", actor="head", query={"period": "all", "detailPageSize": "100"}).payload,
            "dashboard_recent_20": lambda: self.request("GET", "/api/dashboard", actor="head").payload,
        }
        for name, operation in operations.items():
            elapsed, peaks = [], []
            last = None
            for _ in range(3):
                gc.collect()
                tracemalloc.start()
                started = time.perf_counter()
                last = operation()
                elapsed.append((time.perf_counter() - started) * 1000)
                peaks.append(tracemalloc.get_traced_memory()[1])
                tracemalloc.stop()
            metrics[name] = {"iterations": len(elapsed), "elapsedMilliseconds": [round(n, 3) for n in elapsed],
                             "medianMilliseconds": round(statistics.median(elapsed), 3), "peakPythonBytes": max(peaks),
                             "jsonBytes": len(dumps(last).encode("utf-8"))}
            self.assertLess(max(peaks), 32 * 1024 * 1024, "A bounded page must not load the entire report history into Python")
            if name == "orders_page_100":
                self.assertEqual((last["total"], len(last["orders"])), (count, 100))
            elif name == "parts_page_100":
                self.assertEqual((last["total"], len(last["items"])), (count + 1, 100))
            elif name == "performance_details_100":
                self.assertEqual((last["detailTotal"], len(last["details"])), (count, 100))
                self.assertEqual(last["totals"]["score"], Decimal(count))
            else:
                self.assertEqual((last["total"], len(last["recent"])), (count, 20))
                self.assertEqual(last["score"], Decimal(count))
        with self.services.db.connect() as c:
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM pg_constraint WHERE connamespace='public'::regnamespace AND NOT convalidated"), 0)
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM orders"), count)
        result = {"timestampUtc": datetime.now(timezone.utc).isoformat(), "syntheticOnly": True,
                  "liveCompanyDataUsed": False, "httpServerStarted": False, "provider": "PostgreSQL disposable TLS fixture",
                  "python": platform.python_version(), "os": platform.system() + " " + platform.release(),
                  "architecture": platform.machine(), "logicalCpuCount": os.cpu_count(), "reports": count,
                  "parts": count + 1, "seedSeconds": round(seed_seconds, 3), "measurements": metrics,
                  "limits": "In-process router/domain measurements against real isolated PostgreSQL with TLS and tracemalloc; excludes HTTP transport, company LAN latency, concurrent clients and browser rendering. Not a production capacity or speed claim."}
        (PROJECT / "docs/synthetic-load-results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
