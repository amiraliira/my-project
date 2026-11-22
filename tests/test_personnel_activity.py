"""Personnel hall drilldown matches performance participation and keeps it private."""

from decimal import Decimal
import unittest

from pak_maintenance.common import ApiError
from pak_maintenance.performance_service import PerformanceService
from test_activity_reporting import ActivityReportingPostgresqlTests, ActivityReportingTests
from test_api_integration import ApiIntegrationTests


class PersonnelActivityCases:
    seed = ActivityReportingTests.seed

    def hall_report(self, actor, **kwargs):
        return PerformanceService(self.db).hall_report(self.users[actor], **kwargs)

    def test_month_and_all_group_only_final_participation_with_exact_individual_minutes(self):
        mine = self.seed(hall="بخش بسته‌بندی", people=("m1", "m2"))
        self.seed(hall="انبار", people=("m1",), kind="GENERAL", work_date="2025-11-10")
        self.seed(hall="تأسیسات", people=("m1",), status="draft")
        self.seed(hall="سالن همکار", people=("m2",))
        month = self.hall_report("head", person_id="m1", kind="monthly", year=1405, month=7)
        self.assertEqual((month["activityCount"], month["minutes"], month["total"]), (1, 30, 1))
        self.assertEqual(month["items"], [{"hall": "بخش بسته‌بندی", "activityCount": 1, "minutes": 30}])
        details = self.hall_report("m1", kind="monthly", year=1405, month=7,
                              hall="بخش بسته‌بندی", activities=True)
        self.assertEqual([(row["orderId"], row["minutes"]) for row in details["items"]], [(mine, 30)])
        history = self.hall_report("m1", kind="all")
        self.assertEqual((history["activityCount"], history["minutes"], history["total"]), (2, 60, 2))
        self.assertEqual({row["hall"] for row in history["items"]}, {"بخش بسته‌بندی", "انبار"})

    def test_scope_exact_hall_and_page_boundaries(self):
        first = self.seed(hall=" سالن 'عملیات' ", people=("m1",), work_date="2026-10-05")
        last = self.seed(hall=" سالن 'عملیات' ", people=("m1",), work_date="2026-10-10")
        self.seed(hall="", people=("m1",))
        for actor in ("m2", "sm", "planner", "admin"):
            for scope in ({}, {"hall": " سالن 'عملیات' ", "activities": True}):
                with self.subTest(actor=actor, scope=scope), self.assertRaises(ApiError) as caught:
                    self.hall_report(actor, person_id="m1", **scope)
                self.assertEqual(caught.exception.status, 403)
        for page, expected in ((1, last), (2, first)):
            detail = self.hall_report("head", person_id="m1", kind="all",
                                 hall=" سالن 'عملیات' ", activities=True, page=page, page_size=1)
            self.assertEqual(detail["total"], 2)
            self.assertEqual([row["orderId"] for row in detail["items"]], [expected])
        for hall in ("سالن 'عملیات'", "' OR TRUE --"):
            self.assertEqual(self.hall_report("m1", kind="all", hall=hall, activities=True)["total"], 0)
        self.assertEqual(self.hall_report("m1", kind="all", hall="", activities=True)["total"], 1)
        for args in ({"activities": True}, {"page_size": 26}, {"kind": "weekly"}):
            with self.subTest(args=args), self.assertRaises(ApiError) as caught:
                self.hall_report("m1", **args)
            self.assertEqual(caught.exception.status, 400)

    def test_final_activity_includes_consumed_parts_and_workers_without_grades(self):
        with self.db.transaction() as connection:
            connection.execute("INSERT INTO assets(id,code,name,hall,line,body) "
                               "VALUES('drill-pump','DRILL','پمپ','سالن شیر','خط پرکن','{}')")
            connection.execute("INSERT INTO parts(id,code,name,specification,unit,unit_cost) "
                               "VALUES('drill-gasket','DRILL-P','واشر','','عدد',50)")
        body = {
            "type": "CM", "assetId": "drill-pump", "title": "تعویض واشر پمپ",
            "description": "رفع نشتی و بررسی عملکرد", "workDate": "2026-10-10",
            "startTime": "08:00", "endTime": "09:00",
            "participants": [{"personId": "m1", "minutes": 30}, {"personId": "m2", "minutes": 60}],
            "parts": [{"partId": "drill-gasket", "quantity": 2}], "checklist": [],
        }
        row = self.work.save(self.users["m1"], body)
        row = self.work.submit(self.users["m1"], row["id"], row["version"])
        row = self.work.decision(self.users["sm"], row["id"], {
            "version": row["version"], "decision": "approve",
            "grades": [{"personId": "m1", "coefficient": 1}, {"personId": "m2", "coefficient": 2}],
        })
        self.work.decision(self.users["head"], row["id"], {
            "version": row["version"], "decision": "approve",
            "participantScores": [{"personId": "m1", "coefficient": 2}, {"personId": "m2", "coefficient": 3}],
        })
        item = self.hall_report("m1", kind="all", hall="سالن شیر", activities=True)["items"][0]
        self.assertEqual((item["title"], item["description"], item["line"], item["minutes"]),
                         ("تعویض واشر پمپ", "رفع نشتی و بررسی عملکرد", "خط پرکن", 30))
        self.assertEqual(item["parts"], [{"partId": "drill-gasket", "code": "DRILL-P", "name": "واشر",
                                         "quantity": Decimal(2), "unit": "عدد"}])
        self.assertEqual({p["personId"]: p["minutes"] for p in item["participants"]}, {"m1": 30, "m2": 60})
        self.assertTrue(all(set(p) == {"personId", "personName", "minutes"} for p in item["participants"]))
        self.assertEqual(set(item), {"orderId", "workDate", "type", "title", "description", "assetName",
                                     "line", "minutes", "participants", "parts"})


class PersonnelActivityTests(PersonnelActivityCases, unittest.TestCase):
    setUp = ActivityReportingTests.setUp


class PersonnelActivityPostgresqlTests(PersonnelActivityCases, unittest.TestCase):
    password = ActivityReportingPostgresqlTests.password
    setUp = ActivityReportingPostgresqlTests.setUp
    tearDown = ActivityReportingPostgresqlTests.tearDown
    request = ApiIntegrationTests.request
    login = ApiIntegrationTests.login

    def test_routes_forward_the_same_period_and_hold_personnel_boundary(self):
        self.seed(hall="سالن آزمون", people=("m1", "m2"))
        period = {"period": "monthly", "year": "1405", "month": "7"}
        self.login("head")
        self.login("m2")
        halls = self.request("GET", "/api/performance/halls", actor="head",
                             query={**period, "personId": "m1"}).payload
        self.assertEqual((halls["activityCount"], halls["minutes"]), (1, 30))
        drilldown = self.request("GET", "/api/performance/halls/activities", actor="m2",
                                 query={**period, "hall": "سالن آزمون"}).payload
        self.assertEqual((drilldown["personId"], drilldown["items"][0]["minutes"]), ("m2", 60))
        with self.assertRaises(ApiError) as caught:
            self.request("GET", "/api/performance/halls", actor="m2", query={**period, "personId": "m1"})
        self.assertEqual(caught.exception.status, 403)
